"""Twilio voice-webhook server: simulated machines behind real phone numbers.

Point a Twilio number's Voice URL at ``POST /twilio/voice`` (see
``scripts/twilio_number.py``). Each number is a generic "sim line": which scenario the
NEXT inbound call runs is queued via the control endpoint, with an optional
static default per number. Then place a real outbound call from your agent
at the number — real SIP, real carrier audio, real DTMF relay — and fetch the
graded result.

Endpoints:
  POST /twilio/voice        answer webhook → first TwiML segment
  POST /twilio/step         segment continuation (record done / gather result)
  POST /twilio/recording    recording status callback → download the wav
  POST /twilio/status       call status callback → finalize
  GET  /assets/{file}       compiled scenario audio for <Play>
Run through the compatibility entry point:
``python -m telephony_voice_simulator.pstn.server``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from aiohttp import BasicAuth, ClientSession, ClientTimeout, web

from ....control.api import cors
from ....control.identifiers import normalize_e164
from ....control.store import SimulatorStore
from ....paths import ASSETS_DIR, COMPILED_ASSETS_DIR, RESULTS_DIR, SCENARIOS_DIR
from ....pstn.analyze import analyze_call
from ....pstn.compile import (
    DEFAULT_GATHER_TIMEOUT,
    MAX_PROMPT_REPEATS,
    CompiledScenario,
    compile_scenario,
)

COMPILED_DIR = COMPILED_ASSETS_DIR
PSTN_RESULTS_DIR = RESULTS_DIR / "pstn"


class SimState:
    def __init__(self) -> None:
        self.scenarios: dict[str, CompiledScenario] = {}
        self.raw_scenarios: dict[str, dict] = {}
        self.compiled_assets: set[str] = set()
        self.next_by_number: dict[str, deque[str]] = defaultdict(deque)
        self.default_scenario = os.environ.get("PSTN_DEFAULT_SCENARIO", "")
        # Static number->scenario map: a line that ALWAYS answers as one
        # machine, no coordination needed ("call +1425... = stock voicemail").
        # JSON env or file, e.g. PSTN_NUMBER_MAP={"+15551234567":"stock_voicemail_beep_1000"}
        self.static_map: dict[str, str] = {}
        raw_map = os.environ.get("PSTN_NUMBER_MAP", "")
        if raw_map:
            source = Path(raw_map).read_text() if raw_map.endswith(".json") else raw_map
            self.static_map = {str(k): str(v) for k, v in json.loads(source).items()}
        self.calls: dict[str, dict] = {}  # CallSid -> record
        # A deleted Twilio CallSid must not be resurrected by a late recording
        # or status callback. Twilio CallSids are globally unique.
        self.deleted_call_ids: set[str] = set()

    def load_scenarios(self) -> None:
        COMPILED_DIR.mkdir(parents=True, exist_ok=True)
        for stale in COMPILED_DIR.glob("*.wav"):
            if stale.is_file() and not stale.is_symlink():
                stale.unlink()
        self.scenarios.clear()
        self.raw_scenarios.clear()
        self.compiled_assets.clear()
        missing_assets: list[str] = []
        for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text())
            try:
                compiled = compile_scenario(raw, assets_dir=ASSETS_DIR, out_dir=COMPILED_DIR)
            except FileNotFoundError as exc:
                missing = Path(exc.filename).name if exc.filename else str(exc)
                missing_assets.append(f"{raw.get('name')}: {missing}")
                continue
            self.scenarios[compiled.name] = compiled
            self.raw_scenarios[compiled.name] = raw
            self.compiled_assets.update(
                segment.audio_file
                for sequence in compiled.sequences.values()
                for segment in sequence
                if segment.audio_file
            )
        if missing_assets:
            self.scenarios.clear()
            self.raw_scenarios.clear()
            self.compiled_assets.clear()
            preview = ", ".join(missing_assets[:5])
            extra = len(missing_assets) - 5
            suffix = f" (+{extra} more)" if extra > 0 else ""
            raise SystemExit(
                "AMD PSTN corpus is incomplete. Mount a rights-reviewed private corpus and set "
                "SIMULATOR_CORPUS_ASSETS_DIR, or start IVR-only mode with --with-ivr. "
                f"Missing: {preview}{suffix}"
            )
        if not self.scenarios:
            raise SystemExit(
                "No AMD scenarios compiled. Set SIMULATOR_CORPUS_ASSETS_DIR to a "
                "rights-reviewed private corpus, or use --with-ivr."
            )

    def pick_scenario(self, to_number: str) -> str | None:
        # Precedence: queued one-shot > static per-number map > global default.
        queue = self.next_by_number.get(to_number)
        if queue:
            return queue.popleft()
        if to_number in self.static_map:
            return self.static_map[to_number]
        return self.default_scenario or None


STATE = SimState()
CALL_STORE: SimulatorStore | None = None
_TWILIO_CALL_SID = re.compile(r"CA[a-fA-F0-9]{32}")
_LOCAL_CALL_SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


def _call_store() -> SimulatorStore:
    global CALL_STORE
    if CALL_STORE is None:
        CALL_STORE = SimulatorStore()
    return CALL_STORE


def evict_call(call_id: str) -> None:
    """Remove process-local call state and reject late callbacks for this SID."""

    STATE.deleted_call_ids.add(call_id)
    STATE.calls.pop(call_id, None)


def _call_is_deleted(call_id: str) -> bool:
    """Consult both the process cache and persistent privacy tombstones."""

    if call_id in STATE.deleted_call_ids:
        return True
    if _call_store().is_incoming_call_deleted(call_id):
        evict_call(call_id)
        return True
    return False


def _call_sid_is_valid(call_id: str) -> bool:
    """Validate carrier IDs before any cache, database, or filesystem access."""

    if os.environ.get("TWILIO_AUTH_TOKEN", ""):
        return _TWILIO_CALL_SID.fullmatch(call_id) is not None
    return (
        os.environ.get("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "").lower()
        in {"1", "true", "yes"}
        and _LOCAL_CALL_SID.fullmatch(call_id) is not None
        and call_id not in {".", ".."}
    )


def _reject_invalid_call_sid(call_id: str) -> web.Response | None:
    if _call_sid_is_valid(call_id):
        return None
    return web.json_response({"error": "invalid Twilio CallSid"}, status=400)


def _call_results_dir(call_id: str) -> Path:
    """Return the confined direct result directory for a validated call ID."""

    if _LOCAL_CALL_SID.fullmatch(call_id) is None or call_id in {".", ".."}:
        raise ValueError("invalid Twilio CallSid")
    root = PSTN_RESULTS_DIR.resolve()
    candidate = (root / call_id).resolve()
    if candidate.parent != root:
        raise ValueError("invalid Twilio CallSid")
    return candidate


def _normalized_address(address: str) -> str:
    return normalize_e164(address) or address.strip()


def _managed_twilio_matches(address: str) -> list[tuple[object, object]]:
    normalized = _normalized_address(address)
    matches: list[tuple[object, object]] = []
    for endpoint in _call_store().list_endpoints():
        if _normalized_address(endpoint.address) != normalized:
            continue
        connection = _call_store().get_connection(endpoint.connection_id)
        if connection is not None and connection.provider == "twilio":
            matches.append((endpoint, connection))
    return matches


def _twilio_endpoint_is_enabled(address: str) -> bool:
    """Fail closed for a control-plane-managed Twilio address.

    Standalone PSTN deployments may intentionally use only ``PSTN_NUMBER_MAP``
    and have no endpoint rows, so unmatched addresses retain that legacy
    behavior. Once an address is represented by a Twilio endpoint, at least
    one matching endpoint and its provider connection must be enabled.
    """

    matches = _managed_twilio_matches(address)
    managed = any(
        _normalized_address(item) == _normalized_address(address)
        for item in _call_store().list_managed_provider_addresses("twilio")
    )
    return not managed or any(
        endpoint.enabled and connection.enabled for endpoint, connection in matches
    )


def _enabled_twilio_endpoint(address: str):
    return next(
        (
            endpoint
            for endpoint, connection in _managed_twilio_matches(address)
            if endpoint.enabled and connection.enabled
        ),
        None,
    )


def _dequeue_pstn_assignment(address: str) -> dict[str, str | None] | None:
    """Consume a persisted console assignment, including legacy formatting."""

    candidates = [address]
    candidates.extend(
        endpoint.address
        for endpoint, _connection in _managed_twilio_matches(address)
        if endpoint.address not in candidates
    )
    for candidate in candidates:
        assignment = _call_store().dequeue_provider_run("twilio", candidate)
        if assignment is not None:
            return assignment
    queue = STATE.next_by_number.get(address)
    if queue:
        return {"scenario": queue.popleft(), "run_id": None}
    return None


def _pick_call_assignment(address: str) -> dict[str, str | None]:
    """Resolve managed routing first; legacy environment routing is fallback."""

    endpoint = _enabled_twilio_endpoint(address)
    if endpoint is not None:
        queued = _dequeue_pstn_assignment(address)
        if endpoint.routing_mode == "fixed":
            return {
                "scenario": endpoint.default_scenario
                or (queued.get("scenario") if queued is not None else None),
                "run_id": queued.get("run_id") if queued is not None else None,
            }
        if queued is not None:
            return queued
        return {"scenario": endpoint.default_scenario, "run_id": None}
    return {"scenario": STATE.pick_scenario(address), "run_id": None}


def _update_simulation_run(
    run_id: str | None,
    *,
    status: str,
    call_id: str,
    result: dict[str, object] | None = None,
    completed: bool = False,
) -> None:
    if not run_id:
        return
    run = _call_store().get_run(run_id)
    if run is None or run.completed_at is not None:
        return
    merged_result = {**run.result, "mode": "pstn", "call_id": call_id, **(result or {})}
    timeline = [
        *run.timeline,
        {
            "kind": "call",
            "label": "PSTN call completed" if completed else "PSTN call answered",
            "state": status,
            "event": "pstn_call_completed" if completed else "pstn_call_answered",
            "call_id": call_id,
            "at": _iso(),
        },
    ]
    _call_store().update_run(
        run,
        status=status,
        timeline=timeline,
        result=merged_result,
        completed=completed,
    )


def _finish_simulation_run(call: dict, analysis: dict | None = None) -> None:
    run_id = str(call.get("run_id") or "") or None
    if run_id is None:
        return
    carrier_status = str(call.get("status") or "completed")
    result: dict[str, object] = {
        "graded": analysis is not None,
        "carrier_status": carrier_status,
        "duration_seconds": call.get("duration_s"),
    }
    if analysis is not None:
        result.update(
            {
                "passed": analysis.get("passed"),
                "analysis": analysis,
                "summary": (
                    "PSTN call completed and passed AMD checks."
                    if analysis.get("passed")
                    else "PSTN call completed; one or more AMD checks failed."
                ),
            }
        )
    else:
        result["summary"] = (
            "PSTN call reached a terminal carrier status after the runtime restarted; "
            "recording analysis was unavailable."
        )
    _update_simulation_run(
        run_id,
        status="completed" if carrier_status == "completed" else "failed",
        call_id=str(call["call_sid"]),
        result=result,
        completed=True,
    )


def _reject_unavailable_endpoint(
    *,
    call_sid: str,
    from_address: str,
    to_address: str,
) -> web.Response | None:
    if _twilio_endpoint_is_enabled(to_address):
        return None
    candidates = [to_address]
    candidates.extend(
        endpoint.address
        for endpoint, _connection in _managed_twilio_matches(to_address)
        if endpoint.address not in candidates
    )
    for address in candidates:
        _call_store().fail_provider_queue(
            "twilio",
            address,
            "Managed endpoint or provider was unavailable when the PSTN call arrived",
        )
    _call_store().upsert_incoming_call(
        call_id=call_sid,
        from_address=from_address,
        to_address=to_address,
        status="rejected",
        recording_status="not_started",
        analysis={"rejection_reason": "endpoint_or_provider_disabled"},
    )
    return _twiml("<Reject/>")


def _iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time(), UTC).isoformat()


def _epoch(value: str | None, default: float | None = None) -> float:
    if value:
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(value).timestamp()
    return time.time() if default is None else default


def _ivr_max_attempts() -> int:
    try:
        configured = int(os.environ.get("IVR_MAX_ATTEMPTS", "3"))
    except ValueError:
        configured = 3
    return max(1, min(configured, 20))


def _recording_status(result: dict) -> str:
    if result.get("started"):
        return "recording"
    if result.get("disabled"):
        return "disabled"
    return "failed"


def _optional_float(raw: object) -> float | None:
    try:
        return float(str(raw)) if str(raw) else None
    except ValueError:
        return None


def _public(path: str) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}{path}"


def _twilio_request_is_valid(request: web.Request, form: object) -> bool:
    """Validate Twilio's HMAC signature for a webhook request.

    Local unsigned calls require an explicit opt-in. Production receives the
    auth token as part of the normal Twilio configuration.
    """
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not token:
        return os.environ.get("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "").lower() in {
            "1",
            "true",
            "yes",
        }
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature or not hasattr(form, "keys") or not hasattr(form, "getall"):
        return False
    payload = _public(str(request.rel_url))
    # Twilio sorts both field names and repeated values before signing.
    for key in sorted(set(form.keys())):
        for value in sorted(set(form.getall(key))):
            payload += str(key) + str(value)
    expected = base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(signature, expected)


def _reject_invalid_twilio_request(request: web.Request, form: object) -> web.Response | None:
    if _twilio_request_is_valid(request, form):
        return None
    return web.json_response({"error": "invalid Twilio signature"}, status=403)


def _recording_media_url(recording_sid: str) -> str | None:
    """Build, rather than trust, the credentialed recording download URL."""
    account = os.environ.get("TWILIO_ACCOUNT_SID", "")
    if not account or not re.fullmatch(r"RE[a-fA-F0-9]{32}", recording_sid):
        return None
    return (
        f"https://api.twilio.com/2010-04-01/Accounts/{account}"
        f"/Recordings/{recording_sid}"
    )


def _twiml(*verbs: str) -> web.Response:
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(verbs) + "</Response>"
    return web.Response(text=body, content_type="text/xml")


def _play(audio_file: str) -> str:
    return f"<Play>{escape(_public('/assets/' + audio_file))}</Play>"


def _audit_recording_payload() -> dict[str, str]:
    return {
        "RecordingChannels": "dual",
        "RecordingTrack": "both",
        "RecordingStatusCallback": _public("/twilio/recording"),
        "RecordingStatusCallbackMethod": "POST",
        "RecordingStatusCallbackEvent": "completed absent",
    }


def _full_call_recording_enabled(to_number: str) -> bool:
    for number in _call_store().list_provider_numbers(
        provider="twilio",
        managed_mode="amd",
    ):
        if number.phone_number == to_number:
            return number.configuration.get("record_full_calls") is True
    return os.environ.get("PSTN_RECORD_ALL_CALLS", "false").lower() in (
        "1",
        "true",
        "yes",
    )


async def _start_audit_recording(call_sid: str, to_number: str = "") -> dict:
    """Start the full-call audit recording while handling the answer webhook."""
    if not _full_call_recording_enabled(to_number):
        return {"started": False, "disabled": True, "error": "Recording disabled by configuration"}
    account = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not account or not token:
        return {"started": False, "error": "Twilio credentials unavailable"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls/{call_sid}/Recordings.json"
    try:
        async with ClientSession(
            auth=BasicAuth(account, token), timeout=ClientTimeout(total=2.5)
        ) as http:
            async with http.post(
                url,
                data=_audit_recording_payload(),
            ) as resp:
                payload = await resp.json(content_type=None)
                return {
                    "started": resp.status in (200, 201),
                    "http": resp.status,
                    "sid": payload.get("sid"),
                    "status": payload.get("status"),
                }
    except Exception as exc:
        return {"started": False, "error": str(exc)[:160]}


def _live_ivr_analysis(call: dict) -> dict:
    """Serialize live-IVR routing detail into the existing call analysis field."""

    return {
        "flow": "live_ivr",
        "selected_digits": call.get("selected_digits", ""),
        "selected_extension": call.get("selected_extension"),
        "destination": call.get("destination"),
        "dial": call.get("dial", {}),
        "prompt_count": call.get("prompt_count", 0),
        "timeline": call.get("timeline", []),
    }


def _persist_live_ivr(call: dict) -> None:
    if _call_is_deleted(call["call_sid"]):
        return
    ended_at = call.get("ended_at")
    _call_store().upsert_incoming_call(
        call_id=call["call_sid"],
        from_address=call.get("from", ""),
        to_address=call.get("to", ""),
        scenario="ivr_live_directory",
        status=call.get("status", "in-progress"),
        recording_status=call.get("recording_status", "not_started"),
        started_at=_iso(call.get("answered_at")),
        ended_at=_iso(ended_at) if ended_at else None,
        duration_s=call.get("duration_s"),
        analysis=_live_ivr_analysis(call),
    )


def _append_live_ivr_event(call: dict, event: str, **detail: object) -> None:
    payload = {"event": event, **detail}
    timeline = call.setdefault("timeline", [])
    if timeline:
        previous = {key: value for key, value in timeline[-1].items() if key not in {"at", "t"}}
        if previous == payload:
            _persist_live_ivr(call)
            return
    timeline.append(
        {
            "at": _iso(),
            "t": round(time.time() - call["answered_at"], 3),
            **payload,
        }
    )
    _persist_live_ivr(call)


def _live_ivr_call(sid: str, form: object) -> dict:
    """Return live call state, restoring persisted state after a process-local miss."""

    existing = STATE.calls.get(sid)
    if existing is not None and existing.get("kind") == "live_ivr":
        return existing

    persisted = _call_store().get_incoming_call(sid)
    analysis = persisted.analysis if persisted is not None else {}
    call = {
        "kind": "live_ivr",
        "call_sid": sid,
        "to": (
            persisted.to_address
            if persisted is not None
            else str(form.get("To", ""))
            if hasattr(form, "get")
            else ""
        ),
        "from": (
            persisted.from_address
            if persisted is not None
            else str(form.get("From", ""))
            if hasattr(form, "get")
            else ""
        ),
        "scenario": "ivr_live_directory",
        "answered_at": _epoch(persisted.started_at) if persisted is not None else time.time(),
        "status": persisted.status if persisted is not None else "in-progress",
        "recording_status": (
            persisted.recording_status if persisted is not None else "not_started"
        ),
        "recordings": (
            [recording.as_dict() for recording in persisted.recordings]
            if persisted is not None
            else []
        ),
        "selected_digits": analysis.get("selected_digits", ""),
        "selected_extension": analysis.get("selected_extension"),
        "destination": analysis.get("destination"),
        "dial": analysis.get("dial", {}),
        "timeline": list(analysis.get("timeline", [])),
        "prompt_count": int(analysis.get("prompt_count", 0)),
    }
    if persisted is not None:
        if persisted.ended_at:
            call["ended_at"] = _epoch(persisted.ended_at)
        if persisted.duration_s is not None:
            call["duration_s"] = persisted.duration_s
    STATE.calls[sid] = call
    return call


def _segment_twiml(call: dict, seq: str, index: int) -> list[str]:
    """TwiML for one segment + routing to the next step."""
    compiled: CompiledScenario = STATE.scenarios[call["scenario"]]
    segments = compiled.sequences[seq]
    if index >= len(segments):
        return ["<Hangup/>"]
    segment = segments[index]
    call.setdefault("segments", []).append(
        {
            "seq": seq,
            "index": index,
            "t": round(time.time() - call["answered_at"], 3),
            "marks": segment.marks,
            "audio_duration": segment.audio_duration,
            "terminal": segment.terminal,
        }
    )
    verbs: list[str] = []
    action = _public(f"/twilio/step?seq={seq}&index={index}")

    is_first_main = seq == "main" and index == 0
    if compiled.has_gather and is_first_main and call["prompt_plays"] < MAX_PROMPT_REPEATS:
        call["prompt_plays"] += 1
        inner = _play(segment.audio_file) if segment.audio_file else ""
        timeout = int(compiled.gather_timeout or DEFAULT_GATHER_TIMEOUT)
        # finishOnKey="" so '#' is captured as a DIGIT rather than treated as
        # Gather's default terminator (which returns an empty Digits).
        verbs.append(
            f'<Gather input="dtmf" numDigits="1" finishOnKey="" timeout="{timeout}" '
            f'actionOnEmptyResult="true" action="{escape(action + "&gather=1")}" '
            f'method="POST">{inner}</Gather>'
        )
        # Gather always posts to action (actionOnEmptyResult) — no fallthrough.
        return verbs

    if segment.audio_file:
        verbs.append(_play(segment.audio_file))
    if segment.terminal == "dial":
        target = segment.dial_target
        if target == "bridge":
            target = os.environ.get("BRIDGE_NUMBER", "")
        if target:
            # callerId = the sim line, so the bridged phone sees the sim call.
            verbs.append(
                f'<Dial answerOnBridge="true" callerId="{escape(call["to"])}">'
                f"<Number>{escape(target)}</Number></Dial>"
            )
        verbs.append("<Hangup/>")
    elif segment.terminal == "record":
        verbs.append(
            f'<Record playBeep="false" trim="do-not-trim" maxLength="{segment.record_max_s}" '
            f'timeout="6" action="{escape(action)}" method="POST" '
            f'recordingStatusCallback="{escape(_public("/twilio/recording"))}" '
            f'recordingStatusCallbackMethod="POST"/>'
        )
    elif segment.terminal == "hangup":
        verbs.append("<Hangup/>")
    else:  # "end" — advance to the next segment (or hang up at sequence end)
        verbs.append(f'<Redirect method="POST">{escape(action)}</Redirect>')
    return verbs


async def handle_voice(request: web.Request) -> web.Response:
    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return _twiml("<Hangup/>")
    to_number = str(form.get("To", ""))
    if rejection := _reject_unavailable_endpoint(
        call_sid=sid,
        from_address=str(form.get("From", "")),
        to_address=to_number,
    ):
        return rejection
    # Twilio retries voice webhooks. Reuse the already-assigned scenario for
    # the same call SID so a timeout cannot consume another one-shot queue item.
    existing = STATE.calls.get(sid)
    if existing is not None:
        return _twiml(*_segment_twiml(existing, "main", 0))
    persisted = _call_store().get_incoming_call(sid)
    if persisted is not None:
        if persisted.status in {"completed", "failed", "busy", "no-answer", "canceled"}:
            return _twiml("<Hangup/>")
        if persisted.scenario in STATE.scenarios:
            restored = {
                "call_sid": sid,
                "to": persisted.to_address or to_number,
                "from": persisted.from_address or str(form.get("From", "")),
                "scenario": persisted.scenario,
                "answered_at": _epoch(persisted.started_at),
                "digits": [],
                "prompt_plays": 0,
                "recordings": [recording.as_dict() for recording in persisted.recordings],
                "status": persisted.status,
                "run_id": persisted.analysis.get("run_id"),
            }
            STATE.calls[sid] = restored
            return _twiml(*_segment_twiml(restored, "main", 0))
    assignment = _pick_call_assignment(to_number)
    scenario = str(assignment.get("scenario") or "")
    run_id = str(assignment.get("run_id") or "") or None
    if not scenario or scenario not in STATE.scenarios:
        _update_simulation_run(
            run_id,
            status="failed",
            call_id=sid,
            result={
                "graded": False,
                "error": "Managed endpoint has no executable AMD scenario",
            },
            completed=True,
        )
        _call_store().upsert_incoming_call(
            call_id=sid,
            from_address=str(form.get("From", "")),
            to_address=to_number,
            status="rejected",
            recording_status="not_started",
            analysis={
                "run_id": run_id,
                "rejection_reason": "scenario_unavailable",
            },
        )
        return _twiml("<Reject/>")
    call = {
        "call_sid": sid,
        "to": to_number,
        "from": str(form.get("From", "")),
        "scenario": scenario,
        "answered_at": time.time(),
        "digits": [],
        "prompt_plays": 0,
        "recordings": [],
        "status": "in-progress",
        "run_id": run_id,
    }
    STATE.calls[sid] = call
    _call_store().upsert_incoming_call(
        call_id=sid,
        from_address=call["from"],
        to_address=to_number,
        scenario=scenario,
        status="in-progress",
        recording_status="starting",
        started_at=_iso(call["answered_at"]),
        analysis={"run_id": run_id} if run_id else None,
    )
    _update_simulation_run(
        run_id,
        status="in-progress",
        call_id=sid,
        result={"graded": False, "summary": "PSTN call answered; awaiting terminal callback."},
    )
    call["audit_recording_start"] = await _start_audit_recording(sid, to_number)
    _call_store().upsert_incoming_call(
        call_id=sid,
        scenario=scenario,
        status="in-progress",
        recording_status=_recording_status(call["audit_recording_start"]),
    )
    print(f"[voice] {sid} to={to_number} from={call['from']} scenario={scenario}")
    return _twiml(*_segment_twiml(call, "main", 0))


async def handle_ivr_voice(request: web.Request) -> web.Response:
    """Answer a real inbound call with the shared extension directory."""
    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return _twiml("<Hangup/>")
    if rejection := _reject_unavailable_endpoint(
        call_sid=sid,
        from_address=str(form.get("From", "")),
        to_address=str(form.get("To", "")),
    ):
        return rejection
    process_call = STATE.calls.get(sid)
    persisted_call = _call_store().get_incoming_call(sid)
    is_new_call = process_call is None and persisted_call is None
    call = _live_ivr_call(sid, form)
    if is_new_call:
        # Install state before the network await so concurrent Twilio retries
        # cannot start a second full-call recording.
        _append_live_ivr_event(call, "answered")
        audit = await _start_audit_recording(sid, call["to"])
        call["audit_recording_start"] = audit
        call["recording_status"] = _recording_status(audit)
        _persist_live_ivr(call)
    if call.get("ended_at"):
        return _twiml("<Hangup/>")
    max_attempts = _ivr_max_attempts()
    if int(call.get("prompt_count", 0)) >= max_attempts:
        call["status"] = "failed"
        call["ended_at"] = time.time()
        call["duration_s"] = round(call["ended_at"] - call["answered_at"], 1)
        _append_live_ivr_event(call, "retries_exhausted", attempts=max_attempts)
        return _twiml(
            "<Say>We could not complete your request.</Say>",
            "<Hangup/>",
        )
    call["prompt_count"] = int(call.get("prompt_count", 0)) + 1
    _append_live_ivr_event(call, "menu_prompted", attempt=call["prompt_count"])
    action = escape(_public("/twilio/ivr/dial"))
    retry = escape(_public("/twilio/ivr"))
    fallback = (
        "<Say>We could not complete your request.</Say><Hangup/>"
        if call["prompt_count"] >= max_attempts
        else (
            "<Say>We did not receive an extension.</Say>"
            f'<Redirect method="POST">{retry}</Redirect>'
        )
    )
    return _twiml(
        f'<Gather input="dtmf" numDigits="4" timeout="8" action="{action}" method="POST">'
        "<Say>Please enter the extension you wish to reach.</Say>"
        "</Gather>",
        fallback,
    )


async def handle_ivr_dial(request: web.Request) -> web.Response:
    """Resolve gathered digits and bridge to the configured destination."""
    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return _twiml("<Hangup/>")
    call = _live_ivr_call(sid, form)
    digits = str(form.get("Digits", "")).strip()
    call["selected_digits"] = digits
    call["selected_extension"] = None
    call["destination"] = None
    call["dial"] = {}
    _append_live_ivr_event(call, "digits_received", digits=digits)
    valid_extension = re.fullmatch(r"\d{4}", digits) is not None
    entry = _call_store().get_directory_entry_by_extension(digits) if valid_extension else None
    route_connection = (
        _call_store().get_connection(entry.connection_id) if entry is not None else None
    )
    retry = escape(_public("/twilio/ivr"))
    if (
        entry is None
        or not entry.enabled
        or route_connection is None
        or not route_connection.enabled
    ):
        message = (
            "We did not receive an extension."
            if not digits
            else "That extension was not found."
        )
        _append_live_ivr_event(
            call,
            "extension_rejected",
            digits=digits,
            reason=(
                "no_input"
                if not digits
                else "invalid_format"
                if not valid_extension
                else "not_found"
                if entry is None
                else "extension_disabled"
                if not entry.enabled
                else "provider_unavailable"
            ),
        )
        return _twiml(
            f"<Say>{message}</Say>",
            f'<Redirect method="POST">{retry}</Redirect>',
        )

    call["selected_extension"] = {
        "id": entry.id,
        "extension": entry.extension,
        "name": entry.name,
        "department": entry.department,
    }
    call["destination"] = entry.destination
    call["dial"] = {"status": "initiated"}
    _append_live_ivr_event(
        call,
        "dial_started",
        extension=entry.extension,
        destination=entry.destination,
        ring_timeout=entry.ring_timeout,
    )
    caller_id = os.environ.get("IVR_CALLER_ID", "") or str(form.get("To", ""))
    result = escape(_public("/twilio/ivr/result"))
    destination = escape(entry.destination)
    target = (
        f"<Sip>{destination}</Sip>"
        if entry.destination.lower().startswith(("sip:", "sips:"))
        else f"<Number>{destination}</Number>"
    )
    return _twiml(
        f"<Say>Connecting you to {escape(entry.name)}.</Say>",
        f'<Dial callerId="{escape(caller_id)}" answerOnBridge="true" '
        f'timeout="{entry.ring_timeout}" action="{result}" method="POST">'
        f"{target}"
        "</Dial>",
    )


async def handle_ivr_dial_result(request: web.Request) -> web.Response:
    """Persist Twilio's child-leg result before ending the parent call."""

    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return _twiml("<Hangup/>")
    call = _live_ivr_call(sid, form)
    dial_status = str(form.get("DialCallStatus", "") or "unknown")
    dial_duration = _optional_float(form.get("DialCallDuration", ""))
    call["dial"] = {
        "call_sid": str(form.get("DialCallSid", "")) or None,
        "status": dial_status,
        "duration_s": dial_duration,
    }
    if dial_status in {"completed", "busy", "no-answer", "failed", "canceled"}:
        call["status"] = dial_status
        call["ended_at"] = time.time()
        call["duration_s"] = round(call["ended_at"] - call["answered_at"], 1)
    _append_live_ivr_event(
        call,
        "dial_completed",
        status=dial_status,
        child_call_sid=call["dial"]["call_sid"],
        duration_s=dial_duration,
    )
    return _twiml("<Hangup/>")


async def handle_step(request: web.Request) -> web.Response:
    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return _twiml("<Hangup/>")
    call = STATE.calls.get(sid)
    if call is None:
        return _twiml("<Hangup/>")
    seq = request.query.get("seq", "main")
    index = int(request.query.get("index", "0"))
    compiled = STATE.scenarios[call["scenario"]]

    if request.query.get("gather"):
        digits = str(form.get("Digits", "") or "")
        if digits:
            call["digits"].append(
                {"digit": digits, "t": round(time.time() - call["answered_at"], 3)}
            )
            print(f"[dtmf] {sid} digit={digits}")
            spec = compiled.on_dtmf
            if isinstance(spec, dict):
                accepted = [str(d) for d in (spec.get("digits") or [])]
                if not accepted or digits in accepted:
                    return _twiml(*_segment_twiml(call, str(spec["switch"]), 0))
            # "ignore" (or wrong digit): machine doesn't react — re-prompt.
        if call["prompt_plays"] < MAX_PROMPT_REPEATS:
            return _twiml(*_segment_twiml(call, "main", 0))
        return _twiml("<Hangup/>")

    # Record/segment continuation: move to the next segment in this sequence.
    return _twiml(*_segment_twiml(call, seq, index + 1))


async def handle_recording(request: web.Request) -> web.Response:
    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return web.json_response({"ok": True, "deleted": True})
    callback_url = str(form.get("RecordingUrl", ""))
    recording_sid = str(form.get("RecordingSid", ""))
    url = _recording_media_url(recording_sid)
    status = str(
        form.get("RecordingStatus", "") or ("completed" if callback_url else "in-progress")
    )
    source = str(form.get("RecordingSource", ""))
    duration_raw = str(form.get("RecordingDuration", ""))
    duration = _optional_float(duration_raw)
    channels_raw = str(form.get("RecordingChannels", ""))
    channels = int(channels_raw) if channels_raw.isdigit() else None
    call = STATE.calls.get(sid)
    audit_sid = str((call or {}).get("audit_recording_start", {}).get("sid") or "")
    source_key = source.lower()
    kind = (
        "full_call"
        if source_key in ("outboundapi", "startcallrecordingapi")
        or bool(recording_sid and recording_sid == audit_sid)
        else "message"
    )
    if _call_store().get_incoming_call(sid) is None:
        _call_store().upsert_incoming_call(call_id=sid, status="in-progress")
    stored_id = recording_sid or f"{sid}-{kind}-{len((call or {}).get('recordings', [])) + 1}"
    _call_store().upsert_recording(
        recording_id=stored_id,
        call_id=sid,
        provider="twilio",
        kind=kind,
        status=status,
        duration_s=duration,
        channels=channels,
        provider_url=url,
    )
    if kind == "full_call":
        _call_store().upsert_incoming_call(
            call_id=sid,
            status=(call or {}).get("status", "in-progress"),
            recording_status=(
                "completed"
                if status == "completed"
                else "failed"
                if status == "absent"
                else "recording"
            ),
        )
    if call is not None:
        call["recordings"].append(
            {
                "sid": stored_id,
                "url": url,
                "duration": duration,
                "source": source,
                "kind": kind,
                "status": status,
            }
        )
    if url and status == "completed":
        asyncio.create_task(_download_recording(call or {"call_sid": sid}, url, stored_id, kind))
    return web.json_response({"ok": True})


async def _download_recording(call: dict, url: str, recording_sid: str, kind: str) -> None:
    sid = call["call_sid"]
    if _call_is_deleted(sid):
        return
    out_dir = _call_results_dir(sid)
    out_dir.mkdir(parents=True, exist_ok=True)
    auth = BasicAuth(
        os.environ.get("TWILIO_ACCOUNT_SID", ""), os.environ.get("TWILIO_AUTH_TOKEN", "")
    )
    # Twilio recordings become fetchable shortly after the callback.
    async with ClientSession(auth=auth) as http:
        for attempt in range(6):
            await asyncio.sleep(1.5 * attempt)
            with contextlib.suppress(Exception):
                async with http.get(url + ".wav") as resp:
                    if resp.status == 200:
                        if _call_is_deleted(sid):
                            return
                        prefix = "full-call" if kind == "full_call" else "message"
                        path = out_dir / f"{prefix}-{recording_sid}.wav"
                        path.write_bytes(await resp.read())
                        call.setdefault("recording_files", []).append(str(path))
                        if kind == "message":
                            call.setdefault("analysis_recording_files", []).append(str(path))
                        _call_store().upsert_recording(
                            recording_id=recording_sid,
                            call_id=sid,
                            provider="twilio",
                            kind=kind,
                            status="completed",
                            provider_url=url,
                            local_path=str(path),
                        )
                        print(f"[recording] {sid} → {path.name}")
                        return
    if _call_is_deleted(sid):
        return
    _call_store().upsert_recording(
        recording_id=recording_sid,
        call_id=sid,
        provider="twilio",
        kind=kind,
        status="download_failed",
        provider_url=url,
    )
    if kind == "full_call":
        _call_store().upsert_incoming_call(
            call_id=sid,
            status=call.get("status", "completed"),
            recording_status="download_failed",
        )
    print(f"[recording] {sid} FAILED to download {url}")


async def handle_status(request: web.Request) -> web.Response:
    form = await request.post()
    if rejection := _reject_invalid_twilio_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := _reject_invalid_call_sid(sid):
        return rejection
    if _call_is_deleted(sid):
        return web.json_response({"ok": True, "deleted": True})
    status = str(form.get("CallStatus", ""))
    call = STATE.calls.get(sid)
    persisted = _call_store().get_incoming_call(sid)
    terminal = status in ("completed", "failed", "busy", "no-answer", "canceled")
    if persisted is not None:
        ended_at = _iso() if terminal else None
        duration_s = _optional_float(form.get("CallDuration", ""))
        _call_store().upsert_incoming_call(
            call_id=sid,
            status=status or persisted.status,
            ended_at=ended_at,
            duration_s=duration_s,
        )
        if call is None and terminal and persisted.analysis.get("run_id"):
            _finish_simulation_run(
                {
                    "call_sid": sid,
                    "run_id": persisted.analysis["run_id"],
                    "status": status,
                    "duration_s": duration_s,
                }
            )
    if call is not None:
        call["status"] = status
        if call.get("kind") == "live_ivr":
            duration_s = _optional_float(form.get("CallDuration", ""))
            if terminal:
                call["ended_at"] = time.time()
                call["duration_s"] = (
                    duration_s
                    if duration_s is not None
                    else round(call["ended_at"] - call["answered_at"], 1)
                )
            _append_live_ivr_event(
                call,
                "call_status",
                status=status,
                duration_s=duration_s,
            )
            return web.json_response({"ok": True})
        if terminal:
            call["ended_at"] = time.time()
            call["duration_s"] = round(call["ended_at"] - call["answered_at"], 1)

            # Give the recording download a beat, then analyze + persist.
            async def _finalize() -> None:
                await asyncio.sleep(12)
                if _call_is_deleted(sid):
                    return
                raw = STATE.raw_scenarios.get(call["scenario"], {})
                call["analysis"] = analyze_call(call, raw, STATE.scenarios[call["scenario"]])
                if call.get("run_id"):
                    call["analysis"]["run_id"] = call["run_id"]
                out_dir = _call_results_dir(sid)
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "call.json").write_text(json.dumps(call, indent=2, default=str))
                _call_store().upsert_incoming_call(
                    call_id=sid,
                    scenario=call["scenario"],
                    status=call["status"],
                    ended_at=_iso(call.get("ended_at")),
                    duration_s=call.get("duration_s"),
                    analysis=call["analysis"],
                )
                _finish_simulation_run(call, call["analysis"])
                print(
                    f"[done] {sid} scenario={call['scenario']} "
                    f"passed={call['analysis'].get('passed')}"
                )

            asyncio.create_task(_finalize())
    return web.json_response({"ok": True})


async def handle_asset(request: web.Request) -> web.StreamResponse:
    """Serve only files compiled for the active in-process scenario set."""

    filename = request.match_info["file"]
    if Path(filename).name != filename or filename not in STATE.compiled_assets:
        raise web.HTTPNotFound()
    root = COMPILED_DIR.resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file() or path.is_symlink():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


def register_routes(
    app: web.Application,
    *,
    include_amd: bool = True,
    include_ivr: bool = True,
) -> None:
    if include_amd:
        app.router.add_post("/twilio/voice", handle_voice)
        app.router.add_post("/twilio/step", handle_step)
        app.router.add_get("/assets/{file}", handle_asset)
    if include_ivr:
        app.router.add_post("/twilio/ivr", handle_ivr_voice)
        app.router.add_post("/twilio/ivr/dial", handle_ivr_dial)
        app.router.add_post("/twilio/ivr/result", handle_ivr_dial_result)
    # Both runtimes share recording and status callbacks. They are registered
    # once when either mode is active.
    app.router.add_post("/twilio/recording", handle_recording)
    app.router.add_post("/twilio/status", handle_status)


def build_app() -> web.Application:
    app = web.Application(middlewares=[cors])
    register_routes(app)
    return app


def main() -> None:
    # Keep the historical module entry point, but route it through the same
    # HTTPS, signature, and public-audio policy gate as CLI --with-pstn.
    from ....control.cli import _validate_pstn_serve_configuration

    host = os.environ.get("PSTN_BIND_HOST", "127.0.0.1")
    _validate_pstn_serve_configuration(host, include_amd=True)
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    STATE.load_scenarios()
    print(f"scenarios: {', '.join(sorted(STATE.scenarios))}")
    port = int(os.environ.get("PSTN_BIND_PORT", "8978"))
    web.run_app(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
