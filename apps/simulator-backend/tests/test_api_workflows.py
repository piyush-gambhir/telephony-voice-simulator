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


async def test_benchmark_reports_are_addressed_by_id(api, monkeypatch) -> None:
    client, _ = api
    monkeypatch.setattr(benchmarks, "rescore", lambda *, report: {"source": report})
    assert (await client.post("/api/benchmarks/reports/run_1/rescore")).status == 200
    response = await client.post(
        "/api/benchmarks/compare", json={"reports": ["base=../../etc", "run_2"]}
    )
    assert response.status == 400


async def test_agent_rooms_take_catalog_names_only(api) -> None:
    client, _ = api
    response = await client.post("/api/agents/rooms", json={"scenario": "/tmp/x.yaml"})
    assert response.status == 400


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
