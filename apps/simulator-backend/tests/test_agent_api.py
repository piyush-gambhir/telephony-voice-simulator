from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from telephony_voice_simulator import agent_api


def test_dotted_walks_dicts_and_lists() -> None:
    payload = {"a": {"b": [{"c": 7}]}}
    assert agent_api.dotted(payload, "a.b.0.c") == 7
    assert agent_api.dotted(payload, "a.b.9.c") is None
    assert agent_api.dotted(payload, "a.b.-1.c") is None
    assert agent_api.dotted(payload, "a.missing") is None
    assert agent_api.dotted(payload, "") is None


def test_render_substitutes_through_nested_structures() -> None:
    template = {
        "to": "{sim_number}",
        "meta": {"scenario": "{scenario}", "tags": ["run:{run_id}", "static"]},
        "count": 3,
    }
    out = agent_api.render(
        template, {"sim_number": "+15550100000", "scenario": "mailbox_full", "run_id": "r1"}
    )
    assert out == {
        "to": "+15550100000",
        "meta": {"scenario": "mailbox_full", "tags": ["run:r1", "static"]},
        "count": 3,
    }
    # The template itself must be reusable across attempts.
    assert template["to"] == "{sim_number}"


def _config(**overrides) -> agent_api.AgentApiConfig:
    base = {
        "trigger_url": "https://agent.test/calls",
        "body_template": {},
        "status_path": "status",
        "ended_reason_paths": ["providerMetadata.ended_reason"],
        "detection_layer_paths": ["providerMetadata.voicemail_detection_layer"],
        "amd_paths": ["providerMetadata.amd_result"],
    }
    base.update(overrides)
    return agent_api.AgentApiConfig(**base)


def test_record_headers_inherit_unless_explicitly_suppressed() -> None:
    inherits = _config(authorization="Bearer t")
    assert inherits.record_headers == {"Authorization": "Bearer t"}

    overrides = _config(authorization="Bearer t", record_authorization="Basic x")
    assert overrides.record_headers == {"Authorization": "Basic x"}

    # Read paths are often unauthenticated while the write path is not.
    suppressed = _config(authorization="Bearer t", record_authorization="none")
    assert suppressed.record_headers == {}


def test_extract_outcome_reads_the_configured_paths() -> None:
    record = {
        "status": "completed",
        "providerMetadata": {
            "ended_reason": "call.ending.voicemail-left-message",
            "voicemail_detection_layer": "phrase-fallback:mailbox-full",
            "amd_result": {"is_machine": True, "delay": 0.768, "detector": "livekit"},
        },
    }
    outcome = agent_api.extract_outcome(_config(), record)
    assert outcome["status"] == "completed"
    assert outcome["ended_reason"] == "call.ending.voicemail-left-message"
    assert outcome["detection_layer"] == "phrase-fallback:mailbox-full"
    assert outcome["is_machine"] is True
    assert outcome["detection_delay"] == 0.768


def test_extract_outcome_synthesizes_a_layer_from_detector_and_reason() -> None:
    record = {
        "status": "completed",
        "providerMetadata": {
            "ended_reason": "call.ending.voicemail-left-message",
            "amd_result": {"is_machine": True, "detector": "livekit", "reason": "llm"},
        },
    }
    outcome = agent_api.extract_outcome(_config(), record)
    assert outcome["detection_layer"] == "livekit-amd:llm"


def test_extract_outcome_tolerates_a_missing_record() -> None:
    outcome = agent_api.extract_outcome(_config(), None)
    assert outcome["ended_reason"] == ""
    assert outcome["is_machine"] is None
    assert outcome["detection_delay"] is None


def test_load_config_reads_the_body_template(tmp_path, monkeypatch) -> None:
    body = tmp_path / "body.json"
    body.write_text(json.dumps({"_comment": "dropped", "to": "{sim_number}"}))
    monkeypatch.setenv("AGENT_API_TRIGGER_URL", "https://agent.test/calls")
    monkeypatch.setenv("AGENT_API_BODY", str(body))
    monkeypatch.setenv("AGENT_API_CALL_ID_PATH", "data.callId")
    monkeypatch.setenv("AGENT_API_TERMINAL_STATUSES", "completed, ended ")
    config = agent_api.load_config()
    assert config.body_template == {"to": "{sim_number}"}
    assert config.call_id_path == "data.callId"
    assert config.terminal_statuses == ("completed", "ended")


def test_layer_is_never_synthesized_for_a_human_verdict() -> None:
    """A detector that concluded "human" has no detection layer.

    Inventing one from its reason string fails every `detection_layer_absent`
    precision guard, reporting the agent's CORRECT answer as a miss.
    """
    human = {
        "status": "completed",
        "providerMetadata": {
            "ended_reason": "call.in-progress.customer-ended-call",
            "amd_result": {"is_machine": False, "detector": "livekit", "reason": "llm"},
        },
    }
    outcome = agent_api.extract_outcome(_config(), human)
    assert outcome["detection_layer"] == ""
    assert outcome["is_machine"] is False

    machine = {
        "status": "completed",
        "providerMetadata": {
            "ended_reason": "call.ending.voicemail-left-message",
            "amd_result": {"is_machine": True, "detector": "livekit", "reason": "llm"},
        },
    }
    assert agent_api.extract_outcome(_config(), machine)["detection_layer"] == "livekit-amd:llm"

    # An explicit layer always wins, machine or not.
    explicit = {
        "status": "completed",
        "providerMetadata": {
            "ended_reason": "call.ending.voicemail-left-message",
            "voicemail_detection_layer": "phrase-fallback:mailbox-full",
            "amd_result": {"is_machine": True, "detector": "livekit", "reason": "llm"},
        },
    }
    assert (
        agent_api.extract_outcome(_config(), explicit)["detection_layer"]
        == "phrase-fallback:mailbox-full"
    )


@pytest.fixture
def api_clock(monkeypatch):
    clock = {"now": 0.0, "sleeps": []}

    def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(
        agent_api, "time", SimpleNamespace(monotonic=lambda: clock["now"], sleep=sleep)
    )
    return clock


def test_poll_reads_immediately_and_returns_only_terminal_records(monkeypatch, api_clock):
    responses = iter([None, {"status": "queued"}, {"status": "COMPLETED"}])
    monkeypatch.setattr(agent_api, "fetch_record", lambda *_: next(responses))
    config = _config(record_url="https://agent.test/calls/{call_id}")
    result = agent_api.wait_for_terminal(config, "id", timeout=7, interval=3, progress=False)
    assert result == {"status": "COMPLETED"}
    assert api_clock["sleeps"] == [3, 3]


def test_poll_timeout_never_returns_a_nonterminal_snapshot(monkeypatch, api_clock):
    monkeypatch.setattr(agent_api, "fetch_record", lambda *_: {"status": "active"})
    config = _config(record_url="https://agent.test/calls/{call_id}")
    assert agent_api.wait_for_terminal(config, "id", timeout=5, interval=10, progress=False) is None
    assert api_clock["sleeps"] == [5]


@pytest.mark.parametrize("status", [401, 403, 422])
def test_poll_does_not_retry_permanent_http_errors(monkeypatch, api_clock, status):
    def fail(*_):
        httpx.Response(
            status, request=httpx.Request("GET", "https://agent.test/calls/id")
        ).raise_for_status()

    monkeypatch.setattr(agent_api, "fetch_record", fail)
    with pytest.raises(httpx.HTTPStatusError):
        agent_api.wait_for_terminal(_config(record_url="https://agent.test/calls/{call_id}"), "id")
    assert not api_clock["sleeps"]


def test_poll_recovers_after_transient_failure(monkeypatch, api_clock):
    responses = iter([503, 200])

    def record(*_):
        response = httpx.Response(
            next(responses), request=httpx.Request("GET", "https://agent.test/calls/id")
        )
        response.raise_for_status()
        return {"status": "ended"}

    monkeypatch.setattr(agent_api, "fetch_record", record)
    assert agent_api.wait_for_terminal(
        _config(record_url="https://agent.test/calls/{call_id}"), "id", interval=1
    ) == {"status": "ended"}
    assert api_clock["sleeps"] == [1]


def test_http_adapter_renders_trigger_and_encodes_record_identifier(monkeypatch):
    received = []

    def handler(request):
        received.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"callId": "id/with?reserved"})
        return httpx.Response(200, json={"data": {"status": "ended"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(agent_api.httpx, "request", client.request)
        monkeypatch.setattr(agent_api.httpx, "get", client.get)
        config = _config(
            body_template={"to": "{sim_number}", "tag": "{run_id}"},
            record_url="https://agent.test/calls/{call_id}",
            authorization="Bearer test",
        )
        call_id, _ = agent_api.trigger_call(
            config, scenario="mailbox_full", number="+15550000000", run_id="run-1"
        )
        assert agent_api.fetch_record(config, call_id) == {"status": "ended"}
    assert json.loads(received[0].content) == {"to": "+15550000000", "tag": "run-1"}
    assert received[1].url.raw_path == b"/calls/id%2Fwith%3Freserved"
    assert received[1].headers["Authorization"] == "Bearer test"


@pytest.mark.parametrize("payload", [[], "not a record", 1, None])
def test_record_rejects_non_object_json(monkeypatch, payload):
    response = httpx.Response(
        200, json=payload, request=httpx.Request("GET", "https://agent.test/calls/id")
    )
    monkeypatch.setattr(agent_api.httpx, "get", lambda *args, **kwargs: response)
    with pytest.raises(ValueError):
        agent_api.fetch_record(_config(record_url="https://agent.test/calls/{call_id}"), "id")


@pytest.mark.parametrize("delay", [True, -1, float("nan"), float("inf"), "1.5"])
def test_invalid_detector_values_do_not_poison_outcomes(delay):
    outcome = agent_api.extract_outcome(
        _config(), {"providerMetadata": {"amd_result": {"is_machine": "false", "delay": delay}}}
    )
    assert outcome["is_machine"] is None
    assert outcome["detection_delay"] is None


def test_record_only_config_does_not_require_a_trigger(monkeypatch):
    monkeypatch.delenv("AGENT_API_TRIGGER_URL", raising=False)
    monkeypatch.delenv("AGENT_API_BODY", raising=False)
    monkeypatch.setenv("AGENT_API_RECORD_URL", "https://agent.test/calls/{call_id}")
    assert agent_api.load_config(require_trigger=False).body_template == {}
