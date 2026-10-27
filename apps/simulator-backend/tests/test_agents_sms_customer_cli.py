"""Service and CLI wiring for the agents, sms and customer groups."""

from __future__ import annotations

import json
import sqlite3

import pytest

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.integrations import agent_api
from telephony_voice_simulator.service import agents, customer, sms
from telephony_voice_simulator.service.errors import NotFoundError, ValidationError
from telephony_voice_simulator.storage.sqlite import SimulatorStore


def _agent_env(monkeypatch, tmp_path, *, record=True):
    body = tmp_path / "body.json"
    body.write_text(json.dumps({"to": "{sim_number}"}))
    monkeypatch.setenv("AGENT_API_TRIGGER_URL", "https://agent.test/calls")
    monkeypatch.setenv("AGENT_API_BODY", str(body))
    monkeypatch.setenv("SIM_NUMBER", "+15555550100")
    if record:
        monkeypatch.setenv("AGENT_API_RECORD_URL", "https://agent.test/calls/{call_id}")
    else:
        monkeypatch.delenv("AGENT_API_RECORD_URL", raising=False)


def test_missing_agent_configuration_is_a_validation_error(monkeypatch):
    monkeypatch.delenv("AGENT_API_TRIGGER_URL", raising=False)
    with pytest.raises(ValidationError):
        agents.trigger_call()


def test_trigger_and_call_and_record(monkeypatch, tmp_path):
    _agent_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent_api, "trigger_call", lambda config, **kw: ("abc", {"callId": "abc", **kw})
    )
    monkeypatch.setattr(
        agent_api, "fetch_record", lambda config, call_id: {"status": "completed", "id": call_id}
    )
    triggered = agents.trigger_call(scenario="s1")
    assert triggered["call_id"] == "abc" and triggered["number"] == "+15555550100"

    lines: list[str] = []
    result = agents.call_and_wait(scenario="s1", timeout=5, log=lines.append)
    assert result["completed"] is True
    assert result["outcome"]["status"] == "completed"
    assert any("triggered call_id=abc" in line for line in lines)
    assert any("completed" in line for line in lines)

    assert agents.call_record(call_id="abc")["record"]["id"] == "abc"
    monkeypatch.setattr(agent_api, "fetch_record", lambda *_: None)
    with pytest.raises(NotFoundError):
        agents.call_record(call_id="gone")


def test_call_requires_a_record_url(monkeypatch, tmp_path):
    _agent_env(monkeypatch, tmp_path, record=False)
    with pytest.raises(ValidationError, match="AGENT_API_RECORD_URL"):
        agents.call_and_wait()


def test_call_cli_exit_code_follows_completion(monkeypatch, tmp_path, capsys):
    _agent_env(monkeypatch, tmp_path)
    monkeypatch.setattr(agent_api, "trigger_call", lambda config, **kw: ("abc", {}))
    monkeypatch.setattr(agent_api, "fetch_record", lambda *_: {"status": "completed"})
    assert main(["agents", "call", "--scenario", "s1"]) == 0
    assert json.loads(capsys.readouterr().out)["call_id"] == "abc"
    monkeypatch.setattr(agent_api, "wait_for_terminal", lambda *a, **k: None)
    assert main(["agents", "call", "--scenario", "s1"]) == 1


def test_room_resolves_catalog_name_and_path(monkeypatch, tmp_path):
    seen = {}

    async def fake(paths, *, log=None):
        seen["paths"] = paths
        return {"total": len(paths), "passed": len(paths), "results_dir": "x", "reports": []}

    monkeypatch.setattr(agents.runner, "run_scenarios", fake)
    import asyncio

    asyncio.run(agents.run_rooms(scenario="stock_voicemail_beep_1000"))
    assert seen["paths"][0].name == "01_stock_voicemail_beep_1000.yaml"
    custom = tmp_path / "custom.yaml"
    custom.write_text("name: custom\n")
    asyncio.run(agents.run_rooms(scenario=str(custom)))
    assert seen["paths"] == [custom]
    asyncio.run(agents.run_rooms(all_scenarios=True, filter="dtmf"))
    assert seen["paths"] and all("dtmf" in p.name for p in seen["paths"])
    with pytest.raises(NotFoundError):
        asyncio.run(agents.run_rooms(scenario="nope"))
    with pytest.raises(ValidationError):
        asyncio.run(agents.run_rooms())


def _inbox(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE sms_messages (message_sid TEXT PRIMARY KEY, sender TEXT NOT NULL, "
            "recipient TEXT NOT NULL, body TEXT NOT NULL, received_at TEXT NOT NULL)"
        )
        db.execute("INSERT INTO sms_messages VALUES ('SM1','+1','+2','old','2026-01-01T00:00:00')")
        db.execute("INSERT INTO sms_messages VALUES ('SM2','+1','+2','new','2026-01-02T00:00:00')")
    return path


def test_sms_service_and_cli(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("PSTN_SMS_DB", raising=False)
    monkeypatch.setenv("PSTN_SMS_NUMBERS", "+15555550100")
    monkeypatch.setenv("PSTN_SMS_ALLOWED_SENDERS", "+15555550101")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "super-secret")
    assert sms.list_messages() == []
    assert sms.status()["inbox_configured"] is False
    db = tmp_path / "sms.sqlite3"
    monkeypatch.setenv("PSTN_SMS_DB", str(db))
    assert sms.status()["inbox_exists"] is False
    _inbox(db)
    assert main(["sms", "list", "--limit", "1"]) == 0
    assert [m["body"] for m in json.loads(capsys.readouterr().out)] == ["new"]
    assert main(["sms", "status"]) == 0
    out = capsys.readouterr().out
    status = json.loads(out)
    assert status["numbers"] == ["+15555550100"] and status["signing_configured"] is True
    assert "super-secret" not in out
    with pytest.raises(ValidationError):
        sms.list_messages(limit=0)


def test_customer_profiles_and_sessions(monkeypatch, tmp_path, capsys):
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({"+15555550100": "Alex"}))
    monkeypatch.setenv("PSTN_CUSTOMER_PROFILES", str(profiles))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-secret")
    monkeypatch.delenv("ELEVENLABS_CUSTOMER_AGENT_ID", raising=False)
    result = customer.profiles()
    assert result["profiles"] == {"+15555550100": "Alex"}
    assert result["configured"]["ELEVENLABS_API_KEY"] is True
    assert result["configured"]["ELEVENLABS_CUSTOMER_AGENT_ID"] is False
    assert "sk-secret" not in json.dumps(result)

    store = SimulatorStore(tmp_path / "calls.sqlite3")
    store.upsert_incoming_call(call_id="CA1", analysis={})
    store.upsert_incoming_call(
        call_id="CA2", started_at="2026-01-01T00:00:00",
        analysis={"customer_dialogue": [{"role": "user", "content": "hi"}]},
    )
    store.upsert_incoming_call(
        call_id="CA3", started_at="2026-01-02T00:00:00",
        analysis={"customer_dialogue": [], "stopped": "call_limit"},
    )
    sessions = customer.list_sessions(store, limit=10)
    assert [s["call_id"] for s in sessions] == ["CA3", "CA2"]
    assert sessions[0]["stopped"] == "call_limit"
    assert sessions[1]["dialogue"][0]["content"] == "hi"
    assert len(customer.list_sessions(store, limit=1)) == 1

    monkeypatch.setenv("PSTN_CUSTOMER_PROFILES", str(tmp_path / "missing.json"))
    with pytest.raises(ValidationError):
        customer.profiles()
