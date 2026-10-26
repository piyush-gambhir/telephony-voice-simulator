"""Live extension directory: gather four digits, then bridge to the directory destination."""

from __future__ import annotations

import os
import re
import time
from xml.sax.saxutils import escape

from aiohttp import web

from . import state
from .recordings import recording_status, start_audit_recording
from .routing import reject_unavailable_endpoint
from .security import reject_invalid_call_sid, reject_invalid_request
from .state import call_is_deleted, call_store, epoch, iso, optional_float
from .twiml import public_url, twiml


def _ivr_max_attempts() -> int:
    try:
        configured = int(os.environ.get("IVR_MAX_ATTEMPTS", "3"))
    except ValueError:
        configured = 3
    return max(1, min(configured, 20))


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
    if call_is_deleted(call["call_sid"]):
        return
    ended_at = call.get("ended_at")
    call_store().upsert_incoming_call(
        call_id=call["call_sid"],
        from_address=call.get("from", ""),
        to_address=call.get("to", ""),
        scenario="ivr_live_directory",
        status=call.get("status", "in-progress"),
        recording_status=call.get("recording_status", "not_started"),
        started_at=iso(call.get("answered_at")),
        ended_at=iso(ended_at) if ended_at else None,
        duration_s=call.get("duration_s"),
        analysis=_live_ivr_analysis(call),
    )


def append_live_ivr_event(call: dict, event: str, **detail: object) -> None:
    payload = {"event": event, **detail}
    timeline = call.setdefault("timeline", [])
    if timeline:
        previous = {key: value for key, value in timeline[-1].items() if key not in {"at", "t"}}
        if previous == payload:
            _persist_live_ivr(call)
            return
    timeline.append(
        {
            "at": iso(),
            "t": round(time.time() - call["answered_at"], 3),
            **payload,
        }
    )
    _persist_live_ivr(call)


def _live_ivr_call(sid: str, form: object) -> dict:
    """Return live call state, restoring persisted state after a process-local miss."""

    existing = state.STATE.calls.get(sid)
    if existing is not None and existing.get("kind") == "live_ivr":
        return existing

    persisted = call_store().get_incoming_call(sid)
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
        "answered_at": epoch(persisted.started_at) if persisted is not None else time.time(),
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
            call["ended_at"] = epoch(persisted.ended_at)
        if persisted.duration_s is not None:
            call["duration_s"] = persisted.duration_s
    state.STATE.calls[sid] = call
    return call


async def handle_ivr_voice(request: web.Request) -> web.Response:
    """Answer a real inbound call with the shared extension directory."""
    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return twiml("<Hangup/>")
    if rejection := reject_unavailable_endpoint(
        call_sid=sid,
        from_address=str(form.get("From", "")),
        to_address=str(form.get("To", "")),
    ):
        return rejection
    process_call = state.STATE.calls.get(sid)
    persisted_call = call_store().get_incoming_call(sid)
    is_new_call = process_call is None and persisted_call is None
    call = _live_ivr_call(sid, form)
    if is_new_call:
        # Install state before the network await so concurrent Twilio retries
        # cannot start a second full-call recording.
        append_live_ivr_event(call, "answered")
        audit = await start_audit_recording(sid, call["to"])
        call["audit_recording_start"] = audit
        call["recording_status"] = recording_status(audit)
        _persist_live_ivr(call)
    if call.get("ended_at"):
        return twiml("<Hangup/>")
    max_attempts = _ivr_max_attempts()
    if int(call.get("prompt_count", 0)) >= max_attempts:
        call["status"] = "failed"
        call["ended_at"] = time.time()
        call["duration_s"] = round(call["ended_at"] - call["answered_at"], 1)
        append_live_ivr_event(call, "retries_exhausted", attempts=max_attempts)
        return twiml(
            "<Say>We could not complete your request.</Say>",
            "<Hangup/>",
        )
    call["prompt_count"] = int(call.get("prompt_count", 0)) + 1
    append_live_ivr_event(call, "menu_prompted", attempt=call["prompt_count"])
    action = escape(public_url("/twilio/ivr/dial"))
    retry = escape(public_url("/twilio/ivr"))
    fallback = (
        "<Say>We could not complete your request.</Say><Hangup/>"
        if call["prompt_count"] >= max_attempts
        else (
            "<Say>We did not receive an extension.</Say>"
            f'<Redirect method="POST">{retry}</Redirect>'
        )
    )
    return twiml(
        f'<Gather input="dtmf" numDigits="4" timeout="8" action="{action}" method="POST">'
        "<Say>Please enter the extension you wish to reach.</Say>"
        "</Gather>",
        fallback,
    )


async def handle_ivr_dial(request: web.Request) -> web.Response:
    """Resolve gathered digits and bridge to the configured destination."""
    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return twiml("<Hangup/>")
    call = _live_ivr_call(sid, form)
    digits = str(form.get("Digits", "")).strip()
    call["selected_digits"] = digits
    call["selected_extension"] = None
    call["destination"] = None
    call["dial"] = {}
    append_live_ivr_event(call, "digits_received", digits=digits)
    valid_extension = re.fullmatch(r"\d{4}", digits) is not None
    entry = call_store().get_directory_entry_by_extension(digits) if valid_extension else None
    route_connection = (
        call_store().get_connection(entry.connection_id) if entry is not None else None
    )
    retry = escape(public_url("/twilio/ivr"))
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
        append_live_ivr_event(
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
        return twiml(
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
    append_live_ivr_event(
        call,
        "dial_started",
        extension=entry.extension,
        destination=entry.destination,
        ring_timeout=entry.ring_timeout,
    )
    caller_id = os.environ.get("IVR_CALLER_ID", "") or str(form.get("To", ""))
    result = escape(public_url("/twilio/ivr/result"))
    destination = escape(entry.destination)
    target = (
        f"<Sip>{destination}</Sip>"
        if entry.destination.lower().startswith(("sip:", "sips:"))
        else f"<Number>{destination}</Number>"
    )
    return twiml(
        f"<Say>Connecting you to {escape(entry.name)}.</Say>",
        f'<Dial callerId="{escape(caller_id)}" answerOnBridge="true" '
        f'timeout="{entry.ring_timeout}" action="{result}" method="POST">'
        f"{target}"
        "</Dial>",
    )


async def handle_ivr_dial_result(request: web.Request) -> web.Response:
    """Persist Twilio's child-leg result before ending the parent call."""

    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return twiml("<Hangup/>")
    call = _live_ivr_call(sid, form)
    dial_status = str(form.get("DialCallStatus", "") or "unknown")
    dial_duration = optional_float(form.get("DialCallDuration", ""))
    call["dial"] = {
        "call_sid": str(form.get("DialCallSid", "")) or None,
        "status": dial_status,
        "duration_s": dial_duration,
    }
    if dial_status in {"completed", "busy", "no-answer", "failed", "canceled"}:
        call["status"] = dial_status
        call["ended_at"] = time.time()
        call["duration_s"] = round(call["ended_at"] - call["answered_at"], 1)
    append_live_ivr_event(
        call,
        "dial_completed",
        status=dial_status,
        child_call_sid=call["dial"]["call_sid"],
        duration_s=dial_duration,
    )
    return twiml("<Hangup/>")
