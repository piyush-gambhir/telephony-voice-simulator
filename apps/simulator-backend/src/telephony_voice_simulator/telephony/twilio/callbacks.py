"""Recording and call-status callbacks shared by the AMD and IVR runtimes."""

from __future__ import annotations

import time

from aiohttp import web

from . import state
from .amd import persist_runtime
from .ivr import append_live_ivr_event
from .recordings import recording_media_url
from .routing import finish_simulation_run
from .security import reject_invalid_call_sid, reject_invalid_request
from .state import call_is_deleted, call_store, iso, optional_float


async def handle_recording(request: web.Request) -> web.Response:
    from . import finalization

    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return web.json_response({"ok": True, "deleted": True})
    callback_url = str(form.get("RecordingUrl", ""))
    recording_sid = str(form.get("RecordingSid", ""))
    url = recording_media_url(recording_sid)
    status = str(
        form.get("RecordingStatus", "") or ("completed" if callback_url else "in-progress")
    )
    source = str(form.get("RecordingSource", ""))
    duration_raw = str(form.get("RecordingDuration", ""))
    duration = optional_float(duration_raw)
    channels_raw = str(form.get("RecordingChannels", ""))
    channels = int(channels_raw) if channels_raw.isdigit() else None
    call = state.STATE.calls.get(sid) or finalization.restore(sid)
    audit_sid = str((call or {}).get("audit_recording_start", {}).get("sid") or "")
    source_key = source.lower()
    kind = (
        "full_call"
        if source_key in ("outboundapi", "startcallrecordingapi")
        or bool(recording_sid and recording_sid == audit_sid)
        else "message"
    )
    if call_store().get_incoming_call(sid) is None:
        call_store().upsert_incoming_call(call_id=sid, status="in-progress")
    stored_id = recording_sid or f"{sid}-{kind}-{len((call or {}).get('recordings', [])) + 1}"
    previous = call_store().get_recording(stored_id)
    if previous and previous.call_id != sid:
        return web.json_response({"error": "RecordingSid belongs to a different call"}, status=400)
    if call is not None and kind == "message" and "step" in request.query:
        try:
            window = int(request.query["step"])
        except ValueError:
            return web.json_response({"error": "Invalid recording step"}, status=400)
        segments = call.get("segments", [])
        if (not 0 <= window < len(segments) or segments[window]["terminal"] != "record"
                or segments[window].get("gather")):
            return web.json_response({"error": "Invalid recording step"}, status=400)
        windows = call.setdefault("recording_windows", {})
        if stored_id in windows and windows[stored_id] != window:
            return web.json_response({"error": "Recording step changed"}, status=400)
        if any(key != stored_id and value == window for key, value in windows.items()):
            return web.json_response({"error": "Recording step already assigned"}, status=400)
        windows[stored_id] = window
        persist_runtime(call)
    if previous and previous.status in {"completed", "download_failed", "absent"} and status == "in-progress":
        return web.json_response({"ok": True})
    call_store().upsert_recording(
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
        call_store().upsert_incoming_call(
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
        recordings = call.setdefault("recordings", [])
        recordings[:] = [item for item in recordings if item.get("sid", item.get("id")) != stored_id]
        recordings.append(
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
        finalization.start_download(call or {"call_sid": sid}, url, stored_id, kind)
    if call is not None:
        finalization.schedule(call)
    return web.json_response({"ok": True})


async def handle_status(request: web.Request) -> web.Response:
    from . import finalization

    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return web.json_response({"ok": True, "deleted": True})
    status = str(form.get("CallStatus", ""))
    call = state.STATE.calls.get(sid) or finalization.restore(sid)
    persisted = call_store().get_incoming_call(sid)
    terminal = status in finalization.TERMINAL_STATUSES
    queued_persona = request.query.get("persona_queue", "")
    if queued_persona and terminal and persisted is None:
        # A persona dial that never answered never reached the customer webhook, so
        # nothing claimed its persona; drop it so the next call on the line is unaffected.
        call_store().cancel_customer_persona(queued_persona)
    duration_s = optional_float(form.get("CallDuration", ""))
    if persisted is not None:
        if persisted.status in finalization.TERMINAL_STATUSES:
            if not terminal:
                return web.json_response({"ok": True})
        ended_at = (persisted.ended_at or iso()) if terminal else None
        saved = call_store().upsert_incoming_call(
            call_id=sid,
            status=status or persisted.status,
            ended_at=ended_at,
            duration_s=duration_s,
        )
        if saved is None:
            return web.json_response({"ok": True, "deleted": True})
        if call is None and terminal and persisted.analysis.get("run_id"):
            finish_simulation_run(
                {"call_sid": sid, "run_id": persisted.analysis["run_id"],
                 "status": status, "duration_s": duration_s}
            )
    if call is not None:
        call["status"] = status
        if terminal:
            call.setdefault("ended_at", time.time())
            call["duration_s"] = (
                duration_s if duration_s is not None
                else call.get("duration_s", round(call["ended_at"] - call["answered_at"], 1))
            )
        if call.get("kind") == "live_ivr":
            append_live_ivr_event(call, "call_status", status=status, duration_s=duration_s)
        elif terminal:
            finalization.schedule(call)
    return web.json_response({"ok": True})
