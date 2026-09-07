"""Unit tests for PSTN mode: scenario compiler, TwiML flow, analyzer."""

from __future__ import annotations

import json
import base64
import hashlib
import hmac
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from telephony_voice_simulator.machine import SAMPLE_RATE, synth_tone
from telephony_voice_simulator.pstn.analyze import analyze_call
from telephony_voice_simulator.pstn.compile import PSTN_RATE, compile_scenario
from telephony_voice_simulator.pstn import deploy_twilio


@pytest.fixture()
def assets(tmp_path: Path) -> Path:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    sf.write(assets_dir / "greeting.wav", synth_tone(300, 1.0, 8000), SAMPLE_RATE, subtype="PCM_16")
    sf.write(assets_dir / "hello.wav", synth_tone(250, 0.5, 8000), SAMPLE_RATE, subtype="PCM_16")
    return assets_dir


def _vm_scenario() -> dict:
    return {
        "name": "vm",
        "machine": {
            "main": [
                {"wait": 0.5},
                {"play": "greeting.wav"},
                {"wait": 1.0},
                {"tone": {"freq": 1000, "duration": 0.5}},
                {"hold": 30},
            ]
        },
        "expect": {"message_start_after": {"mark": "tone_end", "min": 0.1, "max": 3.0}},
    }


def _gate_scenario(on_dtmf) -> dict:
    return {
        "name": "gate",
        "machine": {
            "main": [{"play": "greeting.wav"}, {"wait": 2.0}, {"repeat_from": 0}],
            "on_dtmf": on_dtmf,
            "connected": [{"play": "hello.wav"}, {"hold": 10}],
        },
        "expect": {"dtmf_received": {"digit": "5"}},
    }


def test_hosted_build_polling_fails_closed_on_timeout() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"status": "building"}

    class Client:
        def get(self, _url: str) -> Response:
            return Response()

    with pytest.raises(
        RuntimeError,
        match=r"BU-timeout \(last status: building\)",
    ):
        deploy_twilio._wait_for_build(
            Client(),  # type: ignore[arg-type]
            "ZS-service",
            "BU-timeout",
            attempts=2,
            delay_s=0,
        )


def test_compile_voicemail_segments_and_marks(assets: Path, tmp_path: Path) -> None:
    out = tmp_path / "compiled"
    compiled = compile_scenario(_vm_scenario(), assets_dir=assets, out_dir=out)
    segments = compiled.sequences["main"]
    # One segment: pre-rendered audio then <Record>. Continuation past the
    # last segment hangs up (covered in the server flow test).
    assert len(segments) == 1
    first = segments[0]
    assert first.terminal == "record"
    assert first.record_max_s == 30
    # tone_end mark: 0.5 wait + 1.0 greeting + 1.0 wait + 0.5 tone = 3.0
    assert abs(first.marks["tone_end"] - 3.0) < 0.05
    assert abs(first.audio_duration - 3.0) < 0.05
    data, rate = sf.read(out / first.audio_file, dtype="int16")
    assert rate == PSTN_RATE
    assert abs(len(data) / rate - 3.0) < 0.05


def test_compile_gate_validates_switch_target(assets: Path, tmp_path: Path) -> None:
    scenario = _gate_scenario({"digits": ["5"], "switch": "nonexistent"})
    with pytest.raises(ValueError):
        compile_scenario(scenario, assets_dir=assets, out_dir=tmp_path / "c")
    compiled = compile_scenario(
        _gate_scenario({"digits": ["5"], "switch": "connected"}),
        assets_dir=assets,
        out_dir=tmp_path / "c",
    )
    assert compiled.has_gather


def test_compile_hangup_scenario(assets: Path, tmp_path: Path) -> None:
    scenario = {
        "name": "full",
        "machine": {"main": [{"play": "greeting.wav"}, {"wait": 1.0}, {"hangup": True}]},
        "expect": {},
    }
    compiled = compile_scenario(scenario, assets_dir=assets, out_dir=tmp_path / "c")
    assert compiled.sequences["main"][0].terminal == "hangup"


def test_compile_handles_machine_options_and_zero_wait(assets: Path, tmp_path: Path) -> None:
    compiled = compile_scenario({
        "name": "bounded", "machine": {"max_duration": 30, "main": [{"wait": 0}, {"hold": 10}]},
        "expect": {},
    }, assets_dir=assets, out_dir=tmp_path / "compiled")
    assert set(compiled.sequences) == {"main"}
    assert compiled.sequences["main"][0].audio_file is None
    assert compiled.sequences["main"][0].terminal == "record"


def test_corrupt_recording_cannot_pass_a_silence_expectation(tmp_path: Path) -> None:
    recording = tmp_path / "broken.wav"
    recording.write_text("not a WAV file")
    analysis = analyze_call({"recording_files": [str(recording)]}, {"expect": {"agent_spoke": False}}, None)
    assert analysis["passed"] is False
    assert any(check["check"] == "recording_readable" and check["passed"] is False for check in analysis["checks"])


def test_unavailable_content_check_keeps_grade_indeterminate(tmp_path: Path, monkeypatch) -> None:
    recording = tmp_path / "mailbox.wav"
    sf.write(recording, synth_tone(220, 1), SAMPLE_RATE, subtype="PCM_16")
    monkeypatch.setattr("telephony_voice_simulator.pstn.analyze.transcribe_recording", lambda path: None)
    analysis = analyze_call({"recording_files": [str(recording)]}, {"expect": {
        "agent_spoke": True, "message_content": {"expected": "Please call me back"},
    }}, None)
    assert analysis["passed"] is None
    assert next(check for check in analysis["checks"] if check["check"] == "agent_spoke")["passed"] is True


async def _post_form(client, path: str, data: dict):
    resp = await client.post(path, data=data)
    assert resp.status == 200
    return await resp.text()


@pytest.fixture()
async def pstn_client(assets: Path, tmp_path: Path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from telephony_voice_simulator.pstn import server as pstn_server
    from telephony_voice_simulator.control.store import SimulatorStore

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sim.example.com")
    monkeypatch.setenv("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "true")
    monkeypatch.setattr(pstn_server, "COMPILED_DIR", tmp_path / "compiled")
    monkeypatch.setattr(pstn_server, "PSTN_RESULTS_DIR", tmp_path / "results")
    state = pstn_server.SimState()
    for scenario in (
        _vm_scenario(),
        _gate_scenario({"digits": ["5"], "switch": "connected"}),
        _gate_scenario("ignore") | {"name": "gate_ignore"},
    ):
        compiled = compile_scenario(scenario, assets_dir=assets, out_dir=tmp_path / "compiled")
        state.scenarios[compiled.name] = compiled
        state.raw_scenarios[compiled.name] = scenario
        state.compiled_assets.update(
            segment.audio_file
            for sequence in compiled.sequences.values()
            for segment in sequence
            if segment.audio_file
        )
    monkeypatch.setattr(pstn_server, "STATE", state)
    monkeypatch.setattr(pstn_server, "CALL_STORE", SimulatorStore(tmp_path / "calls.db"))
    client = TestClient(TestServer(pstn_server.build_app()))
    await client.start_server()
    yield client, state
    await client.close()


async def test_voice_webhook_serves_scenario_twiml(pstn_client) -> None:
    client, state = pstn_client
    state.next_by_number["+15550009999"].append("vm")
    body = await _post_form(
        client, "/twilio/voice", {"CallSid": "CA1", "To": "+15550009999", "From": "+12223334444"}
    )
    assert "<Play>https://sim.example.com/assets/vm-main-0.wav</Play>" in body
    assert '<Record playBeep="false" trim="do-not-trim" maxLength="30"' in body
    # continuation after the record → next segment (end) → hangup path exists
    body = await _post_form(client, "/twilio/step?seq=main&index=0", {"CallSid": "CA1"})
    assert "<Redirect" in body or "<Hangup/>" in body


async def test_asset_route_serves_only_current_compilation_outputs(pstn_client) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, state = pstn_client
    stale = pstn_server.COMPILED_DIR / "stale-private-output.wav"
    stale.write_bytes(b"RIFF-stale-private")

    rejected = await client.get("/assets/stale-private-output.wav")
    active = await client.get("/assets/vm-main-0.wav")

    assert "stale-private-output.wav" not in state.compiled_assets
    assert rejected.status == 404
    assert active.status == 200


async def test_gather_switch_and_ignore(pstn_client) -> None:
    client, state = pstn_client
    state.next_by_number["+1555"].append("gate")
    body = await _post_form(client, "/twilio/voice", {"CallSid": "CA2", "To": "+1555"})
    assert "<Gather" in body and 'numDigits="1"' in body
    body = await _post_form(
        client, "/twilio/step?seq=main&index=0&gather=1", {"CallSid": "CA2", "Digits": "5"}
    )
    assert "gate-connected-0.wav" in body  # switched to connected sequence
    assert state.calls["CA2"]["digits"][0]["digit"] == "5"

    # ignore-mode: digit recorded but machine re-prompts, capped at 3 plays
    state.next_by_number["+1555"].append("gate_ignore")
    await _post_form(client, "/twilio/voice", {"CallSid": "CA3", "To": "+1555"})
    for _ in range(2):
        body = await _post_form(
            client, "/twilio/step?seq=main&index=0&gather=1", {"CallSid": "CA3", "Digits": "5"}
        )
    assert "<Gather" in body  # still prompting (3rd play)
    body = await _post_form(
        client, "/twilio/step?seq=main&index=0&gather=1", {"CallSid": "CA3", "Digits": "5"}
    )
    assert "<Hangup/>" in body  # cap reached
    assert len(state.calls["CA3"]["digits"]) == 3


async def test_unknown_number_rejects(pstn_client) -> None:
    client, _ = pstn_client
    body = await _post_form(client, "/twilio/voice", {"CallSid": "CA4", "To": "+19999999999"})
    assert "<Reject/>" in body


async def test_live_ivr_uses_shared_directory(pstn_client) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, _ = pstn_client
    assert pstn_server.CALL_STORE is not None
    connection = pstn_server.CALL_STORE.create_connection("twilio", "Live IVR")
    pstn_server.CALL_STORE.create_directory_entry(
        connection_id=connection.id,
        extension="1501",
        name="Front desk",
        destination="+15550101501",
        department="Reception",
        ring_timeout=30,
    )

    body = await _post_form(
        client,
        "/twilio/ivr",
        {"CallSid": "CA-ivr", "To": "+15550009999", "From": "+12223334444"},
    )
    assert '<Gather input="dtmf" numDigits="4"' in body
    assert "/twilio/ivr/dial" in body

    body = await _post_form(
        client,
        "/twilio/ivr/dial",
        {"CallSid": "CA-ivr", "To": "+15550009999", "Digits": "1501"},
    )
    assert 'timeout="30"' in body
    assert "<Number>+15550101501</Number>" in body
    assert "/twilio/ivr/result" in body

    body = await _post_form(
        client,
        "/twilio/ivr/result",
        {
            "CallSid": "CA-ivr",
            "DialCallSid": "CA-child",
            "DialCallStatus": "busy",
            "DialCallDuration": "7",
        },
    )
    assert "<Hangup/>" in body
    persisted = pstn_server.CALL_STORE.get_incoming_call("CA-ivr")
    assert persisted is not None
    assert persisted.status == "busy"
    assert persisted.analysis["selected_digits"] == "1501"
    assert persisted.analysis["destination"] == "+15550101501"
    assert persisted.analysis["dial"] == {
        "call_sid": "CA-child",
        "status": "busy",
        "duration_s": 7.0,
    }
    assert [event["event"] for event in persisted.analysis["timeline"]] == [
        "answered",
        "menu_prompted",
        "digits_received",
        "dial_started",
        "dial_completed",
    ]

    await _post_form(
        client,
        "/twilio/status",
        {"CallSid": "CA-ivr", "CallStatus": "completed", "CallDuration": "9"},
    )
    persisted = pstn_server.CALL_STORE.get_incoming_call("CA-ivr")
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.duration_s == 9.0
    status_event = persisted.analysis["timeline"][-1]
    assert status_event["event"] == "call_status"
    assert status_event["status"] == "completed"
    assert status_event["duration_s"] == 9.0

    await _post_form(
        client,
        "/twilio/ivr",
        {"CallSid": "CA-ivr-invalid", "To": "+15550009999", "From": "+12223334444"},
    )
    body = await _post_form(
        client,
        "/twilio/ivr/dial",
        {"CallSid": "CA-ivr-invalid", "To": "+15550009999", "Digits": "9999"},
    )
    assert "That extension was not found." in body
    assert "<Redirect" in body


async def test_live_ivr_rejects_legacy_non_four_digit_extension(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, _ = pstn_client
    assert pstn_server.CALL_STORE is not None
    connection = pstn_server.CALL_STORE.create_connection("twilio", "Legacy IVR")
    # Store-level insertion represents a database created by a pre-migration
    # version; the webhook boundary must still reject it.
    pstn_server.CALL_STORE.create_directory_entry(
        connection_id=connection.id,
        extension="12",
        name="Legacy short extension",
        destination="+15550100012",
        department="Legacy",
        ring_timeout=25,
    )
    await _post_form(
        client,
        "/twilio/ivr",
        {"CallSid": "CA-ivr-short", "To": "+15550009999"},
    )

    body = await _post_form(
        client,
        "/twilio/ivr/dial",
        {"CallSid": "CA-ivr-short", "To": "+15550009999", "Digits": "12"},
    )

    assert "That extension was not found." in body
    assert "<Dial" not in body


async def test_live_ivr_answer_retry_starts_audit_recording_once(
    pstn_client, monkeypatch
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, state = pstn_client
    starts: list[str] = []

    async def started(call_sid: str, _to_number: str = "") -> dict:
        starts.append(call_sid)
        return {"started": True, "sid": "RE-live-ivr", "status": "in-progress"}

    monkeypatch.setattr(pstn_server, "_start_audit_recording", started)
    form = {"CallSid": "CA-ivr-retry", "To": "+15550009999", "From": "+12223334444"}
    await _post_form(client, "/twilio/ivr", form)
    await _post_form(client, "/twilio/ivr", form)

    assert starts == ["CA-ivr-retry"]
    call = state.calls["CA-ivr-retry"]
    assert call["prompt_count"] == 2
    assert [event["event"] for event in call["timeline"]].count("answered") == 1


async def test_live_ivr_retries_are_bounded(pstn_client, monkeypatch) -> None:
    client, state = pstn_client
    monkeypatch.setenv("IVR_MAX_ATTEMPTS", "2")
    form = {"CallSid": "CA-ivr-bounded", "To": "+15550009999", "From": "+12223334444"}

    first = await _post_form(client, "/twilio/ivr", form)
    second = await _post_form(client, "/twilio/ivr", form)
    exhausted = await _post_form(client, "/twilio/ivr", form)

    assert "<Gather" in first
    assert "<Redirect" in first
    assert "<Gather" in second
    assert "<Hangup/>" in second
    assert "<Redirect" not in second
    assert "<Gather" not in exhausted
    assert "<Hangup/>" in exhausted
    call = state.calls["CA-ivr-bounded"]
    assert call["prompt_count"] == 2
    assert call["status"] == "failed"
    assert call["timeline"][-1]["event"] == "retries_exhausted"


async def test_live_ivr_restores_persisted_state_after_process_restart(
    pstn_client, monkeypatch
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, original_state = pstn_client
    starts: list[str] = []

    async def started(call_sid: str, _to_number: str = "") -> dict:
        starts.append(call_sid)
        return {"started": True, "sid": "RE-restored", "status": "in-progress"}

    monkeypatch.setattr(pstn_server, "_start_audit_recording", started)
    form = {"CallSid": "CA-ivr-restored", "To": "+15550009999", "From": "+12223334444"}
    await _post_form(client, "/twilio/ivr", form)
    assert original_state.calls["CA-ivr-restored"]["prompt_count"] == 1

    restarted_state = pstn_server.SimState()
    restarted_state.scenarios = original_state.scenarios
    restarted_state.raw_scenarios = original_state.raw_scenarios
    monkeypatch.setattr(pstn_server, "STATE", restarted_state)

    body = await _post_form(client, "/twilio/ivr", form)

    assert "<Gather" in body
    assert starts == ["CA-ivr-restored"]
    restored = restarted_state.calls["CA-ivr-restored"]
    assert restored["prompt_count"] == 2
    assert restored["to"] == "+15550009999"
    assert restored["from"] == "+12223334444"
    assert [event["event"] for event in restored["timeline"]].count("answered") == 1


async def test_live_ivr_rejects_disabled_provider_routes_and_emits_sip_targets(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, _ = pstn_client
    assert pstn_server.CALL_STORE is not None
    disabled = pstn_server.CALL_STORE.create_connection(
        "twilio",
        "Disabled carrier",
        status="disabled",
    )
    pstn_server.CALL_STORE.create_directory_entry(
        connection_id=disabled.id,
        extension="1701",
        name="Unavailable route",
        destination="+15550101701",
        department="Service",
        ring_timeout=20,
    )
    enabled = pstn_server.CALL_STORE.create_connection("twilio", "SIP carrier")
    pstn_server.CALL_STORE.create_directory_entry(
        connection_id=enabled.id,
        extension="1702",
        name="SIP desk",
        destination="sip:desk@example.test",
        department="Service",
        ring_timeout=25,
    )

    await _post_form(
        client,
        "/twilio/ivr",
        {"CallSid": "CA-disabled-route", "To": "+15550009999"},
    )
    disabled_body = await _post_form(
        client,
        "/twilio/ivr/dial",
        {"CallSid": "CA-disabled-route", "To": "+15550009999", "Digits": "1701"},
    )
    assert "That extension was not found." in disabled_body
    assert "<Dial" not in disabled_body

    await _post_form(
        client,
        "/twilio/ivr",
        {"CallSid": "CA-sip-route", "To": "+15550009999"},
    )
    sip_body = await _post_form(
        client,
        "/twilio/ivr/dial",
        {"CallSid": "CA-sip-route", "To": "+15550009999", "Digits": "1702"},
    )
    assert "<Sip>sip:desk@example.test</Sip>" in sip_body
    assert "<Number>" not in sip_body


async def test_deleted_call_tombstone_survives_restart_and_rejects_late_callback(
    pstn_client,
    monkeypatch,
) -> None:
    from telephony_voice_simulator.control.service import SimulatorService
    from telephony_voice_simulator.control.store import SimulatorStore
    from telephony_voice_simulator.pstn import server as pstn_server

    client, _ = pstn_client
    assert pstn_server.CALL_STORE is not None
    call_id = "CA-deleted-before-restart"
    await _post_form(
        client,
        "/twilio/ivr",
        {"CallSid": call_id, "To": "+15550009999", "From": "+12223334444"},
    )
    database = pstn_server.CALL_STORE.path
    service = SimulatorService(
        store=pstn_server.CALL_STORE,
        recordings_root=pstn_server.PSTN_RESULTS_DIR,
    )
    service.delete_call(call_id)
    pstn_server.evict_call(call_id)
    assert pstn_server.CALL_STORE.is_incoming_call_deleted(call_id)

    restarted_store = SimulatorStore(database)
    restarted_state = pstn_server.SimState()
    monkeypatch.setattr(pstn_server, "CALL_STORE", restarted_store)
    monkeypatch.setattr(pstn_server, "STATE", restarted_state)

    response = await client.post(
        "/twilio/recording",
        data={
            "CallSid": call_id,
            "RecordingSid": "RE" + "a" * 32,
            "RecordingStatus": "completed",
            "RecordingUrl": "https://attacker.invalid/recording",
        },
    )

    assert response.status == 200
    assert await response.json() == {"ok": True, "deleted": True}
    assert restarted_store.is_incoming_call_deleted(call_id)
    assert restarted_store.get_incoming_call(call_id) is None
    assert restarted_store.get_recording("RE" + "a" * 32) is None
    assert call_id not in restarted_state.calls
    assert call_id in restarted_state.deleted_call_ids


async def test_standalone_runtime_exposes_only_provider_routes(pstn_client) -> None:
    client, state = pstn_client
    state.next_by_number["+1555"].append("vm")

    calls = await client.get("/calls")
    assert calls.status == 404
    queued = await client.post(
        "/control/next",
        json={"number": "+1555", "scenario": "vm"},
    )
    assert queued.status == 404

    # Twilio's independently signature-authenticated webhook remains reachable.
    voice = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-public-webhook", "To": "+1555"},
    )
    assert voice.status == 200


async def test_twilio_signature_is_required_when_auth_token_is_set(
    pstn_client, monkeypatch
) -> None:
    client, state = pstn_client
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    state.next_by_number["+1555"].append("vm")
    form = {"CallSid": "CA" + "a" * 32, "To": "+1555"}

    rejected = await client.post("/twilio/voice", data=form)
    assert rejected.status == 403

    payload = "https://sim.example.com/twilio/voice"
    for key in sorted(form):
        payload += key + form[key]
    signature = base64.b64encode(
        hmac.new(b"test-token", payload.encode(), hashlib.sha1).digest()
    ).decode()
    accepted = await client.post(
        "/twilio/voice",
        data=form,
        headers={"X-Twilio-Signature": signature},
    )
    assert accepted.status == 200
    assert "vm-main-0.wav" in await accepted.text()


async def test_managed_twilio_endpoint_and_provider_disable_fail_closed(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, _state = pstn_client
    assert pstn_server.CALL_STORE is not None
    connection = pstn_server.CALL_STORE.create_connection("twilio", "Managed")
    endpoint = pstn_server.CALL_STORE.create_endpoint(
        connection_id=connection.id,
        name="Managed line",
        kind="phone_number",
        address="+15550100001",
    )
    pstn_server.CALL_STORE.update_endpoint(
        endpoint.id,
        connection_id=endpoint.connection_id,
        name=endpoint.name,
        kind=endpoint.kind,
        address=endpoint.address,
        routing_mode=endpoint.routing_mode,
        default_scenario=endpoint.default_scenario,
        enabled=False,
    )

    amd = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-disabled-amd", "To": endpoint.address},
    )
    ivr = await client.post(
        "/twilio/ivr",
        data={"CallSid": "CA-disabled-ivr", "To": endpoint.address},
    )
    assert "<Reject/>" in await amd.text()
    assert "<Reject/>" in await ivr.text()

    enabled_endpoint = pstn_server.CALL_STORE.create_endpoint(
        connection_id=connection.id,
        name="Provider-disabled line",
        kind="phone_number",
        address="+15550100002",
    )
    pstn_server.CALL_STORE.update_connection(
        connection.id,
        name=connection.name,
        status="disabled",
        description=connection.description,
        settings=connection.settings,
    )
    provider_disabled = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-disabled-provider", "To": enabled_endpoint.address},
    )
    assert "<Reject/>" in await provider_disabled.text()


def test_formatted_and_deleted_managed_twilio_numbers_stay_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telephony_voice_simulator.control.store import SimulatorStore
    from telephony_voice_simulator.pstn import server as pstn_server

    store = SimulatorStore(tmp_path / "managed.db")
    monkeypatch.setattr(pstn_server, "CALL_STORE", store)
    connection = store.create_connection("twilio", "Legacy managed")
    endpoint = store.create_endpoint(
        connection_id=connection.id,
        name="Legacy formatted line",
        kind="phone_number",
        address="+1 (555) 010-0001",
    )
    store.update_endpoint(
        endpoint.id,
        connection_id=endpoint.connection_id,
        name=endpoint.name,
        kind=endpoint.kind,
        address=endpoint.address,
        routing_mode=endpoint.routing_mode,
        default_scenario=endpoint.default_scenario,
        enabled=False,
    )

    assert pstn_server._twilio_endpoint_is_enabled("+15550100001") is False
    store.delete_endpoint(endpoint.id)
    assert store.list_managed_twilio_addresses() == ["+1 (555) 010-0001"]
    assert pstn_server._twilio_endpoint_is_enabled("+15550100001") is False


async def test_persisted_queue_tracks_run_through_call_and_completion(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, state = pstn_client
    assert pstn_server.CALL_STORE is not None
    connection = pstn_server.CALL_STORE.create_connection("twilio", "Managed")
    endpoint = pstn_server.CALL_STORE.create_endpoint(
        connection_id=connection.id,
        name="Queued line",
        kind="phone_number",
        address="+15550100003",
        routing_mode="queued",
        default_scenario="gate",
    )
    run = pstn_server.CALL_STORE.create_run(endpoint.id, "twilio", "vm")
    pstn_server.CALL_STORE.enqueue_pstn_run(
        address=endpoint.address,
        scenario="vm",
        run_id=run.id,
    )

    voice = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-run-linked", "To": endpoint.address},
    )
    assert voice.status == 200
    assert "vm-main-0.wav" in await voice.text()
    assert state.calls["CA-run-linked"]["run_id"] == run.id
    assert pstn_server.CALL_STORE.list_pstn_queue(endpoint.address) == []
    in_progress = pstn_server.CALL_STORE.get_run(run.id)
    assert in_progress is not None and in_progress.status == "in-progress"

    pstn_server._finish_simulation_run(
        state.calls["CA-run-linked"] | {"status": "completed", "duration_s": 9},
        {"passed": True, "checks": []},
    )
    completed = pstn_server.CALL_STORE.get_run(run.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.result["passed"] is True
    assert completed.result["call_id"] == "CA-run-linked"
    assert completed.timeline[-1]["label"] == "PSTN call completed"


async def test_fixed_endpoint_without_default_uses_queued_scenario(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, _state = pstn_client
    assert pstn_server.CALL_STORE is not None
    connection = pstn_server.CALL_STORE.create_connection("twilio", "Managed fixed")
    endpoint = pstn_server.CALL_STORE.create_endpoint(
        connection_id=connection.id,
        name="Fixed line without default",
        kind="phone_number",
        address="+15550100004",
        routing_mode="fixed",
    )
    run = pstn_server.CALL_STORE.create_run(endpoint.id, "twilio", "vm")
    pstn_server.CALL_STORE.enqueue_pstn_run(
        address=endpoint.address,
        scenario="vm",
        run_id=run.id,
    )

    response = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-fixed-queued", "To": endpoint.address},
    )
    assert response.status == 200
    assert "vm-main-0.wav" in await response.text()


async def test_voice_retry_after_restart_reuses_persisted_assignment(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, state = pstn_client
    assert pstn_server.CALL_STORE is not None
    connection = pstn_server.CALL_STORE.create_connection("twilio", "Restart")
    endpoint = pstn_server.CALL_STORE.create_endpoint(
        connection_id=connection.id,
        name="Restart-safe queue",
        kind="phone_number",
        address="+15550100005",
        routing_mode="queued",
    )
    first_run = pstn_server.CALL_STORE.create_run(endpoint.id, "twilio", "vm")
    pstn_server.CALL_STORE.enqueue_pstn_run(
        address=endpoint.address,
        scenario="vm",
        run_id=first_run.id,
    )
    first = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-restart-retry", "To": endpoint.address},
    )
    assert "vm-main-0.wav" in await first.text()

    state.calls.pop("CA-restart-retry")
    second_run = pstn_server.CALL_STORE.create_run(endpoint.id, "twilio", "gate")
    pstn_server.CALL_STORE.enqueue_pstn_run(
        address=endpoint.address,
        scenario="gate",
        run_id=second_run.id,
    )
    retry = await client.post(
        "/twilio/voice",
        data={"CallSid": "CA-restart-retry", "To": endpoint.address},
    )

    assert "vm-main-0.wav" in await retry.text()
    assert pstn_server.CALL_STORE.list_pstn_queue(endpoint.address) == [
        {"scenario": "gate", "run_id": second_run.id}
    ]


async def test_call_sid_traversal_is_rejected_before_state_or_filesystem_access(
    pstn_client,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, state = pstn_client
    assert pstn_server.CALL_STORE is not None
    outside = pstn_server.PSTN_RESULTS_DIR.parent / "escaped"

    response = await client.post(
        "/twilio/voice",
        data={"CallSid": "../../escaped", "To": "+1555"},
    )

    assert response.status == 400
    assert "../../escaped" not in state.calls
    assert pstn_server.CALL_STORE.get_incoming_call("../../escaped") is None
    assert not outside.exists()


def test_recording_url_is_constructed_from_sid(monkeypatch) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    sid = "RE" + "a" * 32

    assert pstn_server._recording_media_url(sid) == (
        "https://api.twilio.com/2010-04-01/Accounts/AC123/Recordings/" + sid
    )
    assert pstn_server._recording_media_url("../../attacker") is None


async def test_full_call_recording_defaults_to_disabled(monkeypatch) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    monkeypatch.delenv("PSTN_RECORD_ALL_CALLS", raising=False)
    result = await pstn_server._start_audit_recording("CA-default-off")

    assert result["started"] is False
    assert result["disabled"] is True

    monkeypatch.setenv("PSTN_RECORD_ALL_CALLS", "enable-typo")
    typo_result = await pstn_server._start_audit_recording("CA-still-off")
    assert typo_result["disabled"] is True


async def test_voice_webhook_retry_keeps_same_scenario_assignment(pstn_client) -> None:
    client, state = pstn_client
    state.next_by_number["+1555"].extend(["vm", "gate"])

    first = await _post_form(client, "/twilio/voice", {"CallSid": "CA-retry", "To": "+1555"})
    retry = await _post_form(client, "/twilio/voice", {"CallSid": "CA-retry", "To": "+1555"})
    next_call = await _post_form(client, "/twilio/voice", {"CallSid": "CA-next", "To": "+1555"})

    assert "vm-main-0.wav" in first
    assert "vm-main-0.wav" in retry
    assert "<Gather" in next_call


async def test_incoming_call_and_full_recording_are_persisted(pstn_client, monkeypatch) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    client, state = pstn_client

    async def started(_call_sid: str, _to_number: str = "") -> dict:
        return {"started": True, "sid": "RE-audit", "status": "in-progress"}

    monkeypatch.setattr(pstn_server, "_start_audit_recording", started)
    state.next_by_number["+1555"].append("vm")
    await _post_form(
        client,
        "/twilio/voice",
        {"CallSid": "CA-record", "To": "+1555", "From": "+12223334444"},
    )

    persisted = pstn_server.CALL_STORE.get_incoming_call("CA-record")
    assert persisted is not None
    assert persisted.recording_status == "recording"
    assert persisted.scenario == "vm"

    response = await client.post(
        "/twilio/recording",
        data={
            "CallSid": "CA-record",
            "RecordingSid": "RE-audit",
            "RecordingStatus": "absent",
            "RecordingSource": "StartCallRecordingAPI",
            "RecordingChannels": "2",
        },
    )
    assert response.status == 200
    persisted = pstn_server.CALL_STORE.get_incoming_call("CA-record")
    assert persisted is not None
    assert persisted.recording_status == "failed"
    assert len(persisted.recordings) == 1
    assert persisted.recordings[0].kind == "full_call"
    assert persisted.recordings[0].channels == 2


def test_full_call_recording_requests_completion_callback(monkeypatch) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sim.example.com")

    assert pstn_server._audit_recording_payload() == {
        "RecordingChannels": "dual",
        "RecordingTrack": "both",
        "RecordingStatusCallback": "https://sim.example.com/twilio/recording",
        "RecordingStatusCallbackMethod": "POST",
        "RecordingStatusCallbackEvent": "completed absent",
    }


def test_analyze_leading_silence(tmp_path: Path) -> None:
    rec = tmp_path / "recording-1.wav"
    silence = np.zeros(int(1.0 * PSTN_RATE), dtype=np.int16)
    speech = synth_tone(220, 2.0, 9000, sample_rate=PSTN_RATE)
    sf.write(rec, np.concatenate([silence, speech]), PSTN_RATE, subtype="PCM_16")
    call = {"recording_files": [str(rec)], "digits": []}
    analysis = analyze_call(call, _vm_scenario(), compiled=None)
    check = analysis["checks"][0]
    assert check["check"] == "message_start_after"
    assert check["passed"], check["detail"]
    assert analysis["passed"] is True


def test_analyze_no_speech_fails(tmp_path: Path) -> None:
    rec = tmp_path / "recording-1.wav"
    sf.write(rec, np.zeros(PSTN_RATE * 2, dtype=np.int16), PSTN_RATE, subtype="PCM_16")
    call = {"recording_files": [str(rec)], "digits": []}
    analysis = analyze_call(call, _vm_scenario(), compiled=None)
    assert analysis["passed"] is False


def test_message_content_rejects_leaked_agent_preamble(tmp_path: Path, monkeypatch) -> None:
    rec = tmp_path / "recording.wav"
    sf.write(rec, synth_tone(220, 1.0, 9000, sample_rate=PSTN_RATE), PSTN_RATE)
    expected = "Hello, this is Chris calling about your vehicle recall."
    scenario = {
        "expect": {
            "message_content": {
                "expected": expected,
                "min_recall": 0.75,
                "head_words": 5,
            }
        }
    }
    monkeypatch.setattr(
        "telephony_voice_simulator.pstn.analyze.transcribe_recording",
        lambda _path: "Hi Jordan, may I speak with you? " + expected,
    )

    analysis = analyze_call({"recording_files": [str(rec)]}, scenario, compiled=None)

    assert analysis["passed"] is False
    assert "preamble_words=7" in analysis["checks"][0]["detail"]


def test_message_content_allows_one_transcription_filler_word(tmp_path: Path, monkeypatch) -> None:
    rec = tmp_path / "recording.wav"
    sf.write(rec, synth_tone(220, 1.0, 9000, sample_rate=PSTN_RATE), PSTN_RATE)
    expected = "Hello, this is Chris calling about your vehicle recall."
    scenario = {
        "expect": {
            "message_content": {
                "expected": expected,
                "allowed_identity_name": "Jordan",
            }
        }
    }
    monkeypatch.setattr(
        "telephony_voice_simulator.pstn.analyze.transcribe_recording",
        lambda _path: "Well, " + expected,
    )

    analysis = analyze_call({"recording_files": [str(rec)]}, scenario, compiled=None)

    assert analysis["passed"] is True


def test_message_content_allows_configured_identity_question(tmp_path: Path, monkeypatch) -> None:
    rec = tmp_path / "recording.wav"
    sf.write(rec, synth_tone(220, 1.0, 9000, sample_rate=PSTN_RATE), PSTN_RATE)
    expected = "Hello, this is Chris calling about your vehicle recall."
    scenario = {
        "expect": {
            "message_content": {
                "expected": expected,
                "allowed_identity_name": "Jordan",
            }
        }
    }
    monkeypatch.setattr(
        "telephony_voice_simulator.pstn.analyze.transcribe_recording",
        lambda _path: "Hi, is this Jordan? " + expected,
    )

    analysis = analyze_call({"recording_files": [str(rec)]}, scenario, compiled=None)

    assert analysis["passed"] is True
    assert "allowed identity question" in analysis["checks"][0]["detail"]


def test_message_content_rejects_identity_question_with_extra_speech(
    tmp_path: Path, monkeypatch
) -> None:
    rec = tmp_path / "recording.wav"
    sf.write(rec, synth_tone(220, 1.0, 9000, sample_rate=PSTN_RATE), PSTN_RATE)
    expected = "Hello, this is Chris calling about your vehicle recall."
    scenario = {
        "expect": {
            "message_content": {
                "expected": expected,
                "allowed_identity_name": "Jordan",
            }
        }
    }
    monkeypatch.setattr(
        "telephony_voice_simulator.pstn.analyze.transcribe_recording",
        lambda _path: "Hi, is this Jordan, how are you? " + expected,
    )

    analysis = analyze_call({"recording_files": [str(rec)]}, scenario, compiled=None)

    assert analysis["passed"] is False


def test_full_call_audit_recording_is_not_used_for_mailbox_timing(tmp_path: Path) -> None:
    full = tmp_path / "recording-0-startcallrecordingapi.wav"
    mailbox = tmp_path / "recording-1-recordverb.wav"
    speech = synth_tone(220, 2.0, 9000, sample_rate=PSTN_RATE)
    sf.write(full, speech, PSTN_RATE, subtype="PCM_16")
    sf.write(
        mailbox,
        np.concatenate([np.zeros(PSTN_RATE, dtype=np.int16), speech]),
        PSTN_RATE,
        subtype="PCM_16",
    )
    call = {
        "recording_files": [str(full), str(mailbox)],
        "analysis_recording_files": [str(mailbox)],
        "digits": [],
    }

    analysis = analyze_call(call, _vm_scenario(), compiled=None)

    assert analysis["passed"] is True
    assert analysis["recordings_analyzed"] == [str(mailbox)]
    assert analysis["recordings_preserved"] == [str(full), str(mailbox)]


def test_analyze_dtmf_checks() -> None:
    call = {"recording_files": [], "digits": [{"digit": "5", "t": 4.0}]}
    scenario = {"expect": {"dtmf_received": {"digit": "5"}, "dtmf_press_count_max": 2}}
    analysis = analyze_call(call, scenario, compiled=None)
    assert analysis["passed"] is True, json.dumps(analysis)


async def test_static_number_map(pstn_client) -> None:
    client, state = pstn_client
    state.static_map["+15551230000"] = "vm"
    # No queue entry needed — the static line always answers as its machine.
    body = await _post_form(client, "/twilio/voice", {"CallSid": "CA9", "To": "+15551230000"})
    assert "vm-main-0.wav" in body
    # A queued one-shot overrides the static mapping for exactly one call.
    state.next_by_number["+15551230000"].append("gate")
    body = await _post_form(client, "/twilio/voice", {"CallSid": "CA10", "To": "+15551230000"})
    assert "<Gather" in body
    body = await _post_form(client, "/twilio/voice", {"CallSid": "CA11", "To": "+15551230000"})
    assert "vm-main-0.wav" in body  # back to the static machine


def test_compile_dial_bridge(assets: Path, tmp_path: Path) -> None:
    scenario = {
        "name": "bridge",
        "machine": {
            "main": [{"play": "greeting.wav"}, {"wait": 2.0}, {"repeat_from": 0}],
            "on_dtmf": {"digits": ["#"], "switch": "connected"},
            "connected": [{"dial": "bridge"}],
        },
        "expect": {},
    }
    compiled = compile_scenario(scenario, assets_dir=assets, out_dir=tmp_path / "c")
    segment = compiled.sequences["connected"][0]
    assert segment.terminal == "dial"
    assert segment.dial_target == "bridge"


async def test_pound_gate_captured_and_bridged(pstn_client, monkeypatch, assets, tmp_path) -> None:
    client, state = pstn_client
    monkeypatch.setenv("BRIDGE_NUMBER", "+15559998888")
    scenario = {
        "name": "bridge",
        "machine": {
            "main": [{"play": "greeting.wav"}, {"wait": 1.0}, {"repeat_from": 0}],
            "on_dtmf": {"digits": ["#"], "switch": "connected"},
            "connected": [{"dial": "bridge"}],
        },
        "expect": {},
    }
    compiled = compile_scenario(scenario, assets_dir=assets, out_dir=tmp_path / "compiled")
    state.scenarios["bridge"] = compiled
    state.raw_scenarios["bridge"] = scenario
    state.next_by_number["+1555"].append("bridge")
    voice = await _post_form(client, "/twilio/voice", {"CallSid": "CA20", "To": "+1555"})
    # finishOnKey="" is what lets '#' arrive as a Digit instead of terminating.
    assert 'finishOnKey=""' in voice
    body = await _post_form(
        client, "/twilio/step?seq=main&index=0&gather=1", {"CallSid": "CA20", "Digits": "#"}
    )
    assert '<Dial answerOnBridge="true" callerId="+1555">' in body
    assert "<Number>+15559998888</Number>" in body
    assert state.calls["CA20"]["digits"][0]["digit"] == "#"


def test_transcribe_prefers_elevenlabs_then_openai(monkeypatch, tmp_path) -> None:
    """Backend selection must not depend on which key happens to be set."""
    from telephony_voice_simulator.pstn import analyze

    wav = tmp_path / "message.wav"
    wav.write_bytes(b"RIFF")
    calls: list[str] = []

    monkeypatch.setattr(
        analyze, "_transcribe_elevenlabs", lambda p, k: calls.append("11labs") or "eleven"
    )
    monkeypatch.setattr(
        analyze, "_transcribe_openai", lambda p, k: calls.append("openai") or "open"
    )

    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    assert analyze.transcribe_recording(wav) == "eleven"
    assert calls == ["11labs"]

    # A flaky ElevenLabs call falls through rather than losing the check.
    calls.clear()
    monkeypatch.setattr(analyze, "_transcribe_elevenlabs", lambda p, k: None)
    assert analyze.transcribe_recording(wav) == "open"
    assert calls == ["openai"]

    # No backend configured is indeterminate, not a failure.
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert analyze.transcribe_recording(wav) is None


def test_transcribe_returns_none_for_a_missing_file(tmp_path, monkeypatch) -> None:
    from telephony_voice_simulator.pstn import analyze

    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    assert analyze.transcribe_recording(tmp_path / "nope.wav") is None
