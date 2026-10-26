"""Hosted-callee service: queue consolidation, grading and CLI wiring (no network)."""

import asyncio
import json

import httpx
import pytest

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.integrations.livekit import agent_call as livekit_agent_call
from telephony_voice_simulator.service import ValidationError, hosted
from telephony_voice_simulator.telephony.twilio.hosted import grade as grade_hosted


class FakeSync:
    """A Sync document store behind httpx.MockTransport, with revisions."""

    def __init__(self, doc: dict | None = None) -> None:
        self.doc = doc
        self.revision = 1
        self.writes: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if self.doc is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": self.doc, "revision": str(self.revision)})
        form = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        from urllib.parse import unquote_plus

        self.doc = json.loads(unquote_plus(form["Data"]))
        self.writes.append({"if_match": request.headers.get("If-Match")})
        self.revision += 1
        return httpx.Response(200, json={})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture()
def known_scenarios(monkeypatch):
    monkeypatch.setattr(grade_hosted, "_load_scenario", lambda name: {"name": name})


def test_queue_appends_and_keeps_claims(known_scenarios) -> None:
    sync = FakeSync({"queue": ["first"], "claims": {"CA1": "first"}})
    queue = grade_hosted.queue_scenario(sync.client(), "+14255550100", "second")
    assert queue == ["first", "second"]
    assert sync.doc == {"queue": ["first", "second"], "claims": {"CA1": "first"}}
    assert sync.writes[0]["if_match"] == "1"


def test_agent_call_queues_by_appending_and_keeps_claims(known_scenarios, monkeypatch) -> None:
    sync = FakeSync({"queue": ["pending"], "claims": {"CA9": "pending"}})
    monkeypatch.setenv("SIM_NUMBER", "+14255550100")
    monkeypatch.setenv("AGENT_NAME", "agent")
    monkeypatch.setenv("LIVEKIT_URL", "wss://x")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    monkeypatch.setattr(grade_hosted, "twilio_client", lambda: (sync.client(), "AC1"))
    monkeypatch.setattr(livekit_agent_call, "twilio_client", lambda: (sync.client(), "AC1"))
    dispatched = {}

    async def fake_dispatch(**kwargs):
        dispatched.update(kwargs)

    monkeypatch.setattr(livekit_agent_call, "create_dispatch", fake_dispatch)
    lines: list[str] = []
    result = asyncio.run(
        hosted.agent_call(scenario="wanted", queue=True, wait=False, log=lines.append)
    )
    assert sync.doc == {"queue": ["pending", "wanted"], "claims": {"CA9": "pending"}}
    assert dispatched["agent_name"] == "agent"
    assert result["sim_number"] == "+14255550100"
    assert result["passed"] is None and result["call"] is None
    assert any("dispatched" in line for line in lines)


def test_agent_call_no_queue_leaves_the_queue_alone(known_scenarios, monkeypatch) -> None:
    sync = FakeSync({"queue": ["pending"]})
    monkeypatch.setenv("SIM_NUMBER", "+14255550100")
    monkeypatch.setenv("AGENT_NAME", "agent")
    monkeypatch.setenv("LIVEKIT_URL", "wss://x")
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")
    monkeypatch.setattr(livekit_agent_call, "twilio_client", lambda: (sync.client(), "AC1"))

    async def fake_dispatch(**_kwargs):
        return None

    monkeypatch.setattr(livekit_agent_call, "create_dispatch", fake_dispatch)
    asyncio.run(hosted.agent_call(scenario="wanted", queue=False, wait=False))
    assert sync.doc == {"queue": ["pending"]} and not sync.writes


def test_missing_agent_environment_is_a_validation_error(monkeypatch) -> None:
    monkeypatch.delenv("SIM_NUMBER", raising=False)
    with pytest.raises(ValidationError, match="SIM_NUMBER"):
        asyncio.run(hosted.agent_call(queue=False, wait=False))


def test_grade_requires_a_target_and_credentials(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        hosted.grade_calls()
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    with pytest.raises(ValidationError, match="TWILIO_ACCOUNT_SID"):
        hosted.grade_calls(call_sid="CA1")


def test_grade_by_number_grades_inbound_legs_and_skips_non_sim_calls(monkeypatch) -> None:
    def call_for(sid):
        return {"call_sid": sid, "analysis": {"passed": True, "checks": []}}

    legs = [{"sid": "CA1", "direction": "inbound"}, {"sid": "CA2", "direction": "inbound"}]
    monkeypatch.setattr(grade_hosted, "twilio_client", lambda: (httpx.Client(), "AC1"))
    monkeypatch.setattr(grade_hosted, "inbound_legs", lambda *_a: legs)
    monkeypatch.setattr(
        grade_hosted, "grade_call_detail",
        lambda _c, _a, sid, **_k: call_for(sid) if sid == "CA1" else None,
    )
    result = hosted.grade_calls(number="+14255550100", last=2)
    assert result["passed"] is True
    assert [c["call_sid"] for c in result["calls"]] == ["CA1"]
    assert result["skipped"] == ["CA2"]


def test_cli_hosted_queue_prints_the_queue(known_scenarios, monkeypatch, capsys) -> None:
    sync = FakeSync(None)
    monkeypatch.setattr(grade_hosted, "twilio_client", lambda: (sync.client(), "AC1"))
    assert main(["hosted", "queue", "--number", "+14255550100", "--scenario", "s1"]) == 0
    assert json.loads(capsys.readouterr().out)["queue"] == ["s1"]


def test_cli_hosted_deploy_rejects_bad_static_json(capsys) -> None:
    assert main(["hosted", "deploy", "--static", "{nope"]) == 1
    assert "--static" in capsys.readouterr().err


def test_cli_hosted_deploy_refuses_without_acknowledgement(capsys) -> None:
    assert main(["hosted", "deploy"]) == 1
    assert "acknowledge-public-audio" in capsys.readouterr().err


def test_cli_hosted_agent_call_flags(monkeypatch, capsys) -> None:
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return {"passed": None, "call": None}

    monkeypatch.setattr(hosted, "agent_call", lambda **kw: fake(**kw))
    assert main(["hosted", "agent-call", "--scenario", "x", "--no-queue", "--no-wait"]) == 0
    assert seen["scenario"] == "x" and seen["queue"] is False and seen["wait"] is False
