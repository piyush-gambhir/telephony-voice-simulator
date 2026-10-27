"""API routes for hosted Twilio, account numbers, agents, benchmarks, UAT, corpus and inbox.

Calls that would reach Twilio, LiveKit or a TTS engine are faked: these tests cover
the HTTP contract (status codes, 202 operations, path confinement), not the services.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.api import common as api_common
from telephony_voice_simulator.api.app import build_app
from telephony_voice_simulator.service import SimulatorService
from telephony_voice_simulator.service import agents, benchmarks, corpus, hosted, inventory, uat
from telephony_voice_simulator.storage.sqlite import SimulatorStore


@pytest.fixture()
async def api(tmp_path: Path):
    service = SimulatorService(store=SimulatorStore(tmp_path / "api.db"))
    async with TestClient(TestServer(build_app(service))) as client:
        yield client, service
    if service._runner is not None:
        service._runner.shutdown()


async def _settled(client, operation: dict) -> dict:
    for _ in range(200):
        current = await (await client.get(f"/api/operations/{operation['id']}")).json()
        if current["status"] in {"succeeded", "failed"}:
            return current
        await asyncio.sleep(0.01)
    raise AssertionError("operation did not finish")


async def test_long_jobs_answer_202_and_stream_their_log(api, monkeypatch) -> None:
    client, _ = api

    def fake_smoke(*, number, scenario, from_number, log):
        log(f"dialing {number}")
        return {"passed": True, "calls": [{"scenario": scenario}]}

    monkeypatch.setattr(hosted, "smoke_call", fake_smoke)
    response = await client.post(
        "/api/hosted/smoke", json={"number": "+15550100001", "scenario": "mailbox_full"}
    )
    assert response.status == 202
    finished = await _settled(client, await response.json())
    assert finished["status"] == "succeeded"
    assert finished["log"] == ["dialing +15550100001"]
    assert finished["result"]["passed"] is True


async def test_async_service_jobs_run_as_operations(api, monkeypatch) -> None:
    client, _ = api

    async def fake_agent_call(*, scenario, queue, wait, log):
        log("dispatched")
        return {"scenario": scenario, "queued": queue, "passed": None}

    monkeypatch.setattr(hosted, "agent_call", fake_agent_call)
    response = await client.post("/api/hosted/agent-call", json={"wait": False})
    finished = await _settled(client, await response.json())
    assert finished["result"] == {
        "scenario": hosted.DEFAULT_SCENARIO, "queued": True, "passed": None,
    }


async def test_account_number_backups_are_bare_names_in_one_directory(
    api, monkeypatch, tmp_path: Path
) -> None:
    client, _ = api
    monkeypatch.setattr(api_common, "NUMBER_BACKUP_DIR", tmp_path / "twilio")
    seen = {}

    async def fake_restore(*, number, backup, dry_run):
        seen["backup"] = backup
        return {"number": number, "applied": not dry_run}

    monkeypatch.setattr(inventory, "restore_number", fake_restore)
    for bad in ("../secrets.json", "/etc/passwd", ".hidden.json"):
        response = await client.post(
            "/api/account-numbers/revert", json={"number": "+15550100001", "backup": bad}
        )
        assert response.status == 400, bad

    response = await client.post(
        "/api/account-numbers/revert",
        json={"number": "+15550100001", "backup": "PN123.json", "dry_run": True},
    )
    assert response.status == 200
    assert seen["backup"] == tmp_path / "twilio" / "PN123.json"


async def test_config_files_are_named_not_pathed(api, monkeypatch, tmp_path: Path) -> None:
    client, _ = api
    monkeypatch.setattr(api_common, "UAT_CONFIG_DIR", tmp_path)
    (tmp_path / "smoke.yaml").write_text("matrix: []\n")
    checked = {}
    monkeypatch.setattr(uat, "check_config", lambda *, config: checked.setdefault("c", config))

    assert (await client.post("/api/uat/check", json={"config": "../uat.yaml"})).status == 400
    assert (await client.post("/api/uat/check", json={"config": "missing.yaml"})).status == 404
    assert (await client.post("/api/uat/check", json={"config": "smoke.yaml"})).status == 200
    assert checked["c"] == tmp_path / "smoke.yaml"


async def test_benchmark_run_validates_before_queueing(api, monkeypatch) -> None:
    client, service = api

    def refuse(**_options):
        raise benchmarks.ValidationError("No simulator line configured")

    monkeypatch.setattr(benchmarks, "plan", refuse)
    response = await client.post("/api/benchmarks/runs", json={"scenarios": ["mailbox_full"]})
    assert response.status == 400
    assert service.list_operations() == []


async def test_benchmark_reports_resolve_by_id_inside_the_results_directory(
    api, monkeypatch, tmp_path: Path
) -> None:
    from telephony_voice_simulator.evaluation.benchmark import reports as report_files

    client, _ = api
    results = tmp_path / "benchmark"
    (results / "run_1").mkdir(parents=True)
    (results / "run_1" / "report.json").write_text('{"suite": "amd-core"}')
    monkeypatch.setattr(report_files, "BENCHMARK_RESULTS_DIR", results)
    (tmp_path / "secret.json").write_text('{"token": "x"}')
    monkeypatch.chdir(tmp_path)

    assert (await (await client.get("/api/benchmarks/reports/run_1")).json()) == {"suite": "amd-core"}
    for probe in ("secret.json", "..%2Fsecret.json", "%2Fetc%2Fhosts", "run_2"):
        response = await client.get(f"/api/benchmarks/reports/{probe}")
        assert response.status in {400, 404}, probe
    response = await client.post("/api/benchmarks/compare", json={"reports": ["base=secret.json", "run_1"]})
    assert response.status == 404

    seen = {}
    monkeypatch.setattr(benchmarks, "rescore", lambda *, report: seen.setdefault("r", report))
    assert (await client.post("/api/benchmarks/reports/run_1/rescore")).status == 200
    assert seen["r"] == str((results / "run_1" / "report.json").resolve())


async def test_scenario_selection_takes_catalog_names_only(api, tmp_path: Path, monkeypatch) -> None:
    client, _ = api
    (tmp_path / "notes.yaml").write_text("secret: value\n")
    monkeypatch.chdir(tmp_path)
    for path in ("/tmp/x.yaml", "notes.yaml"):
        assert (await client.post("/api/agents/rooms", json={"scenario": path})).status == 404
        response = await client.post("/api/scenarios/validate", json={"scenario": path})
        assert response.status == 404
        assert "secret" not in await response.text()


async def test_corpus_builds_and_imports_never_overlap(api, monkeypatch) -> None:
    import threading

    client, _ = api
    release = threading.Event()
    monkeypatch.setattr(corpus, "build_corpus", lambda **_kw: release.wait(5))
    started = await client.post("/api/corpus/build", json={})
    response = await client.post(
        "/api/corpus/import",
        json={"asset": "a.wav", "source": "x", "source_type": corpus.SOURCE_TYPES[0], "license_note": "n"},
    )
    release.set()
    assert started.status == 202
    assert response.status == 409
    await _settled(client, await started.json())


async def test_agent_transport_failures_are_409_not_500(api, monkeypatch) -> None:
    import httpx

    from telephony_voice_simulator.integrations import agent_api

    client, _ = api
    monkeypatch.setattr(agent_api, "load_config", lambda **_kw: agent_api.AgentApiConfig(trigger_url="https://agent.example.test/calls", body_template={}))
    monkeypatch.setattr(agent_api, "sim_number", lambda: "+15550100001")

    def unreachable(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(agent_api, "trigger_call", unreachable)
    response = await client.post("/api/agents/trigger", json={})
    assert response.status == 409
    assert "Agent API request failed" in (await response.json())["error"]


async def test_agent_record_maps_missing_records_to_404(api, monkeypatch) -> None:
    client, _ = api

    def missing(*, call_id):
        raise agents.NotFoundError(f"No agent call record for {call_id}")

    monkeypatch.setattr(agents, "call_record", missing)
    assert (await client.get("/api/agents/calls/abc")).status == 404


async def test_corpus_rejects_unknown_source_types_and_engines(api) -> None:
    client, _ = api
    response = await client.post(
        "/api/corpus/import",
        json={"asset": "a.wav", "source": "x", "source_type": "pirated", "license_note": "n"},
    )
    assert response.status == 400
    response = await client.post("/api/corpus/prompts/render", json={"engine": "nope"})
    assert response.status == 400
    assert set(corpus.SOURCE_TYPES) and set(corpus.PROMPT_ENGINES)


async def test_inbox_reads_and_silence_analysis(api, monkeypatch) -> None:
    client, _ = api
    monkeypatch.delenv("PSTN_SMS_DB", raising=False)
    assert await (await client.get("/api/sms/messages")).json() == []
    assert (await (await client.get("/api/sms/status")).json())["inbox_configured"] is False
    assert await (await client.get("/api/customer/sessions")).json() == []
    assert (await client.get("/api/sms/messages?limit=many")).status == 400

    response = await client.post(
        "/api/analysis/silence",
        json={"rows": [
            {"call_uid": "1", "user_text": "Please leave a message after the tone"},
            {"call_uid": "2", "user_text": "Press one for sales"},
        ]},
    )
    summary = await response.json()
    assert response.status == 200
    assert summary["total"] == 2
    assert (await client.post("/api/analysis/silence", json={"rows": "x"})).status == 400


async def test_large_analysis_uploads_fit_the_request_limit(api) -> None:
    client, _ = api
    rows = [{"call_uid": str(index), "user_text": "x" * 240} for index in range(5000)]
    response = await client.post("/api/analysis/silence", json={"rows": rows})
    assert response.status == 200
