"""Scripted answering-machine callee: answer, play segments, gather DTMF, serve audio."""

from __future__ import annotations

import os
import time
from pathlib import Path
from xml.sax.saxutils import escape

from aiohttp import web

from . import state
from .compile import (
    DEFAULT_GATHER_TIMEOUT,
    MAX_PROMPT_REPEATS,
    CompiledScenario,
)
from .recordings import recording_status, start_audit_recording
from .routing import pick_call_assignment, reject_unavailable_endpoint, update_simulation_run
from .security import reject_invalid_call_sid, reject_invalid_request
from .state import call_is_deleted, call_store, epoch, iso
from .twiml import play, public_url, twiml


def _call_scenario(call: dict) -> CompiledScenario:
    if "grading_spec" in call:
        return CompiledScenario.from_dict(call["grading_spec"]["compiled"])
    return state.STATE.scenarios[call["scenario"]]


def compiled_rejects(call: dict) -> bool:
    """True when the scenario turns the call away before answering."""
    main = _call_scenario(call).sequences.get("main") or []
    return bool(main) and main[0].terminal == "reject"


def _segment_twiml(call: dict, seq: str, index: int) -> list[str]:
    """TwiML for one segment + routing to the next step."""
    compiled = _call_scenario(call)
    segments = compiled.sequences[seq]
    if index >= len(segments):
        return ["<Hangup/>"]
    segment = segments[index]
    step = len(call.get("segments", []))
    gather = seq == "main" and index == 0 and compiled.has_gather and call["prompt_plays"] < MAX_PROMPT_REPEATS
    call.setdefault("segments", []).append(
        {
            "step": step,
            "gather": gather,
            "seq": seq,
            "index": index,
            "t": round(time.time() - call["answered_at"], 3),
            "marks": segment.marks,
            "audio_duration": segment.audio_duration,
            "terminal": segment.terminal,
        }
    )
    verbs: list[str] = []
    action = public_url(f"/twilio/step?seq={seq}&index={index}&step={step}")

    is_first_main = seq == "main" and index == 0
    if compiled.has_gather and is_first_main and call["prompt_plays"] < MAX_PROMPT_REPEATS:
        call["prompt_plays"] += 1
        inner = play(segment.audio_file) if segment.audio_file else ""
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

    if segment.terminal == "reject":
        # Validation keeps this to the first main step, so the line never answers.
        return [f'<Reject reason="{escape(segment.reject_reason or "rejected")}"/>']
    if segment.audio_file:
        verbs.append(play(segment.audio_file))
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
            f'timeout="0" finishOnKey="" action="{escape(action)}" method="POST" '
            f'recordingStatusCallback="{escape(public_url(f"/twilio/recording?step={step}"))}" '
            f'recordingStatusCallbackMethod="POST"/>'
        )
    elif segment.terminal == "hangup":
        verbs.append("<Hangup/>")
    else:  # "end" — advance to the next segment (or hang up at sequence end)
        verbs.append(f'<Redirect method="POST">{escape(action)}</Redirect>')
    return verbs


def _runtime_analysis(call: dict) -> dict:
    """Analysis payload that lets a restarted process resume this exact execution."""
    from .finalization import snapshot

    analysis: dict = {"runtime": snapshot(call)}
    if call.get("run_id"):
        analysis["run_id"] = call["run_id"]
    if call.get("queue_claim"):
        # Every analysis write keeps the claim, so a concurrent retry never dequeues again.
        analysis["queue_claim"] = call["queue_claim"]
    return analysis


def persist_runtime(call: dict) -> None:
    # Terminal calls belong to finalization, which owns the analysis field from then on.
    if call.get("status") in {"completed", "failed", "busy", "no-answer", "canceled"}:
        return
    call_store().upsert_incoming_call(call_id=call["call_sid"], analysis=_runtime_analysis(call))


async def handle_voice(request: web.Request) -> web.Response:
    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return twiml("<Hangup/>")
    to_number = str(form.get("To", ""))
    if rejection := reject_unavailable_endpoint(
        call_sid=sid,
        from_address=str(form.get("From", "")),
        to_address=to_number,
    ):
        return rejection
    # Twilio retries voice webhooks. Reuse the already-assigned scenario for
    # the same call SID so a timeout cannot consume another one-shot queue item.
    existing = state.STATE.calls.get(sid)
    if existing is not None:
        if existing.get("status") in {"completed", "failed", "busy", "no-answer", "canceled"}:
            return twiml("<Hangup/>")
        if "voice_response" not in existing:
            existing["voice_response"] = twiml(*_segment_twiml(existing, "main", 0)).text
        return web.Response(text=existing["voice_response"], content_type="application/xml")
    persisted = call_store().get_incoming_call(sid)
    if persisted is not None:
        if persisted.status in {"completed", "failed", "busy", "no-answer", "canceled"}:
            return twiml("<Hangup/>")
        from .finalization import restore

        # Resume the saved execution (frozen program, digits, cached replies). A row
        # holding only a queue claim falls through and reuses that claim below.
        restored = restore(sid)
        if restored is not None and "voice_response" in restored:
            return web.Response(text=restored["voice_response"], content_type="application/xml")
        if (restored is None and "queue_claim" not in persisted.analysis
                and persisted.scenario in state.STATE.scenarios):
            # Rows saved before runtime snapshots existed keep their original scenario.
            restored = {
                "call_sid": sid,
                "to": persisted.to_address or to_number,
                "from": persisted.from_address or str(form.get("From", "")),
                "scenario": persisted.scenario,
                "answered_at": epoch(persisted.started_at),
                "digits": [],
                "prompt_plays": 0,
                "recordings": [recording.as_dict() for recording in persisted.recordings],
                "status": persisted.status,
                "run_id": persisted.analysis.get("run_id"),
            }
            state.STATE.calls[sid] = restored
            restored["voice_response"] = twiml(*_segment_twiml(restored, "main", 0)).text
            return web.Response(text=restored["voice_response"], content_type="application/xml")
    assignment = pick_call_assignment(to_number, sid)
    scenario = str(assignment.get("scenario") or "")
    run_id = str(assignment.get("run_id") or "") or None
    if not scenario or scenario not in state.STATE.scenarios:
        update_simulation_run(
            run_id,
            status="failed",
            call_id=sid,
            result={
                "graded": False,
                "error": "Managed endpoint has no executable AMD scenario",
            },
            completed=True,
        )
        call_store().upsert_incoming_call(
            call_id=sid,
            from_address=str(form.get("From", "")),
            to_address=to_number,
            status="rejected",
            recording_status="not_started",
            analysis={
                "run_id": run_id,
                "queue_claim": assignment.get("queue_claim"),
                "rejection_reason": "scenario_unavailable",
            },
        )
        return twiml("<Reject/>")
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
    if assignment.get("queue_claim"):
        call["queue_claim"] = assignment["queue_claim"]
    state.STATE.calls[sid] = call
    from .finalization import capture_scenario

    capture_scenario(call)
    # Prepare the initial reply before awaiting the recording API so retries
    # cannot advance the scenario while that request is in flight.
    call["voice_response"] = twiml(*_segment_twiml(call, "main", 0)).text
    call_store().upsert_incoming_call(
        call_id=sid,
        from_address=call["from"],
        to_address=to_number,
        scenario=scenario,
        status="in-progress",
        recording_status="starting",
        started_at=iso(call["answered_at"]),
        analysis=_runtime_analysis(call),
    )
    update_simulation_run(
        run_id,
        status="in-progress",
        call_id=sid,
        result={"graded": False, "summary": "PSTN call answered; awaiting terminal callback."},
    )
    if compiled_rejects(call):
        # A rejected call is never answered, so there is nothing to record.
        print(f"[voice] {sid} to={to_number} from={call['from']} scenario={scenario} rejected")
        return web.Response(text=call["voice_response"], content_type="application/xml")
    call["audit_recording_start"] = await start_audit_recording(sid, to_number)
    if call_is_deleted(sid) or call.get("status") in {"completed", "failed", "busy", "no-answer", "canceled"}:
        return twiml("<Hangup/>")
    call_store().upsert_incoming_call(
        call_id=sid,
        scenario=scenario,
        status="in-progress",
        recording_status=recording_status(call["audit_recording_start"]),
    )
    print(f"[voice] {sid} to={to_number} from={call['from']} scenario={scenario}")
    return web.Response(text=call["voice_response"], content_type="application/xml")


async def handle_step(request: web.Request) -> web.Response:
    form = await request.post()
    if rejection := reject_invalid_request(request, form):
        return rejection
    sid = str(form.get("CallSid", ""))
    if rejection := reject_invalid_call_sid(sid):
        return rejection
    if call_is_deleted(sid):
        return twiml("<Hangup/>")
    from .finalization import restore

    call = state.STATE.calls.get(sid) or restore(sid)
    if call is None:
        return twiml("<Hangup/>")
    if call.get("status") in {"completed", "failed", "busy", "no-answer", "canceled"}:
        return twiml("<Hangup/>")
    seq = request.query.get("seq", "main")
    try:
        index = int(request.query.get("index", "0"))
    except ValueError:
        return twiml("<Hangup/>")
    compiled = _call_scenario(call)
    if compiled is None or seq not in compiled.sequences or not 0 <= index < len(compiled.sequences[seq]):
        return twiml("<Hangup/>")

    step = request.query.get("step")
    if step is not None:
        try:
            step_index = int(step)
        except ValueError:
            return twiml("<Hangup/>")
        step = str(step_index)
        emitted = call.get("segments", [])
        if not 0 <= step_index < len(emitted):
            return twiml("<Hangup/>")
        entry = emitted[step_index]
        if (entry["seq"] != seq or entry["index"] != index
                or entry.get("gather", False) != bool(request.query.get("gather"))):
            return twiml("<Hangup/>")
        cached = call.get("step_responses", {}).get(step)
        if cached is not None:
            return web.Response(text=cached, content_type="application/xml")
        if step_index != len(emitted) - 1:
            return twiml("<Hangup/>")

    def transition(verbs: list[str]) -> web.Response:
        response = twiml(*verbs)
        if step is not None:
            call.setdefault("step_responses", {})[step] = response.text
        persist_runtime(call)
        return response

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
                    return transition(_segment_twiml(call, str(spec["switch"]), 0))
            # "ignore" (or wrong digit): machine doesn't react — re-prompt.
        if call["prompt_plays"] < MAX_PROMPT_REPEATS:
            return transition(_segment_twiml(call, "main", 0))
        return transition(["<Hangup/>"])

    # Record/segment continuation: move to the next segment in this sequence.
    return transition(_segment_twiml(call, seq, index + 1))


async def handle_asset(request: web.Request) -> web.StreamResponse:
    """Serve only files compiled for the active in-process scenario set."""

    filename = request.match_info["file"]
    if Path(filename).name != filename or filename not in state.STATE.compiled_assets:
        raise web.HTTPNotFound()
    root = state.COMPILED_DIR.resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file() or path.is_symlink():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "audio/wav"})
