"""Recording-aware grading jobs, persisted in the existing call repository."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path

from aiohttp import web

from ....assertions import summarize_checks
from ....pstn.analyze import analyze_call
from ....pstn.compile import CompiledScenario
from . import webhooks as runtime

CALLBACK_GRACE_SECONDS = 12
TERMINAL_STATUSES = {"completed", "failed", "busy", "no-answer", "canceled"}
MEDIA_CHECKS = {"agent_spoke", "message_start_after", "message_content"}


def capture_scenario(call: dict) -> None:
    if "grading_spec" not in call:
        call["grading_spec"] = {
            "scenario": copy.deepcopy(runtime.STATE.raw_scenarios[call["scenario"]]),
            "compiled": asdict(runtime.STATE.scenarios[call["scenario"]]),
        }


def snapshot(call: dict) -> dict:
    return copy.deepcopy({key: value for key, value in call.items() if key != "analysis"})


def restore(sid: str) -> dict | None:
    """Recover only a saved AMD execution, never reconstruct playback from a catalog."""
    if not runtime._call_sid_is_valid(sid) or runtime._call_is_deleted(sid):
        return None
    if sid in runtime.STATE.calls:
        return runtime.STATE.calls[sid]
    saved = runtime._call_store().get_incoming_call(sid)
    data = saved.analysis.get("finalization", {}).get("runtime") if saved else None
    if not isinstance(data, dict) or not isinstance(data.get("grading_spec"), dict):
        return None
    call = copy.deepcopy(data)
    call.update(call_sid=sid, status=saved.status, analysis=saved.analysis)
    runtime.STATE.calls[sid] = call
    return call


def media_state(call: dict) -> tuple[list, list[str], bool, dict]:
    """Fingerprint persisted message media and confine files before reading them."""
    saved = runtime._call_store().get_incoming_call(call["call_sid"])
    if saved is None:
        return [], [], True, {}
    root = runtime._call_results_dir(call["call_sid"]).resolve()
    windows = call.get("recording_windows", {})
    emitted = [(i, segment) for i, segment in enumerate(call.get("segments", []))
               if segment["terminal"] == "record" and not segment.get("gather")]
    messages = sorted((r for r in saved.recordings if r.kind == "message"),
                      key=lambda r: (windows.get(r.id, float("inf")), r.id))
    ordered = (all(r.id in windows for r in messages)
               or (len(messages) <= 1 and len(emitted) <= 1))
    evidence = {"message_recording_order_known": ordered}
    signature, paths = [], []
    incomplete = not messages
    for recording in messages:
        path = Path(recording.local_path) if recording.local_path else None
        readable = bool(
            path and not path.is_symlink() and path.resolve().parent == root and path.is_file()
        )
        signature.append([recording.id, recording.status, recording.local_path, readable,
                          windows.get(recording.id)])
        if readable:
            paths.append(str(path))
        elif recording.status != "absent":
            incomplete = True
    if ordered and emitted:
        first_step, first_segment = emitted[0]
        first = next((r for r in messages if windows.get(r.id) == first_step), None)
        if first is None and len(messages) == 1 and len(emitted) == 1 and not windows:
            first = messages[0]
        evidence["message_recording_file"] = (
            first.local_path if first and first.local_path in paths else None
        )
        evidence["message_recording_window"] = first_segment
        if first is None:
            incomplete = True
    return signature, paths, incomplete, evidence


def schedule(call: dict) -> None:
    sid = call["call_sid"]
    if (call.get("kind") == "live_ivr" or call.get("status") not in TERMINAL_STATUSES
            or ("grading_spec" not in call and call.get("scenario") not in runtime.STATE.scenarios)
            or runtime._call_is_deleted(sid)):
        return
    active = runtime.STATE.finalization_tasks.get(sid)
    if active and not active.done():
        return
    signature, _, _, _ = media_state(call)
    previous = call.get("analysis", {}).get("finalization", {})
    if previous.get("state") == "complete" and previous.get("media_signature") == signature:
        return
    capture_scenario(call)
    analysis = {**call.get("analysis", {}), "graded": False, "passed": None,
                "finalization": {"state": "pending", "runtime": snapshot(call)}}
    if runtime._call_store().upsert_incoming_call(call_id=sid, analysis=analysis) is None:
        return
    call["analysis"] = analysis
    run = runtime._call_store().get_run(call["run_id"]) if call.get("run_id") else None
    if run is not None and run.completed_at is not None:
        runtime._finish_simulation_run(call, analysis)
    state = runtime.STATE
    task = asyncio.create_task(finalize(call))
    state.finalization_tasks[sid] = task

    def finished(done: asyncio.Task) -> None:
        if state.finalization_tasks.get(sid) is done:
            state.finalization_tasks.pop(sid, None)
        if not done.cancelled():
            done.exception()  # finalize persists errors; consume unexpected task failures too.

    task.add_done_callback(finished)


def start_download(call: dict, url: str, recording_sid: str, kind: str) -> None:
    sid = call["call_sid"]
    state = runtime.STATE
    if recording_sid in state.recording_tasks.get(sid, {}):
        return
    saved = runtime._call_store().get_recording(recording_sid)
    if saved and saved.status == "completed" and saved.local_path:
        path = Path(saved.local_path)
        root = runtime._call_results_dir(sid).resolve()
        if not path.is_symlink() and path.resolve().parent == root and path.is_file():
            schedule(call)
            return
    tasks = state.recording_tasks.setdefault(sid, {})
    task = asyncio.create_task(runtime._download_recording(call, url, recording_sid, kind))
    tasks[recording_sid] = task

    def finished(done: asyncio.Task) -> None:
        tasks.pop(recording_sid, None)
        if not tasks:
            state.recording_tasks.pop(sid, None)
        if not done.cancelled():
            done.exception()
            schedule(call)

    task.add_done_callback(finished)


async def finalize(call: dict) -> None:
    sid = call["call_sid"]
    try:
        # Allow callbacks to arrive, then await actual downloads rather than guessing their duration.
        await asyncio.sleep(CALLBACK_GRACE_SECONDS)
        while not runtime._call_is_deleted(sid):
            tasks = list(runtime.STATE.recording_tasks.get(sid, {}).values())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                if runtime._call_is_deleted(sid):
                    return
            signature, paths, incomplete, evidence = media_state(call)
            work = snapshot(call)
            work["analysis_recording_files"] = paths
            work.update(evidence)
            raw = call["grading_spec"]["scenario"]
            spec = call["grading_spec"]["compiled"]
            compiled = CompiledScenario.from_dict(spec)
            analysis = await asyncio.to_thread(analyze_call, work, raw, compiled)
            if runtime._call_is_deleted(sid):
                return
            current_signature, _, _, _ = media_state(call)
            if signature != current_signature or runtime.STATE.recording_tasks.get(sid):
                continue
            needs_media = bool(MEDIA_CHECKS & raw.get("expect", {}).keys())
            if incomplete and needs_media:
                for check in analysis["checks"]:
                    if check["check"] in MEDIA_CHECKS:
                        check.update(passed=None, detail="message media unavailable; awaiting provider evidence")
                analysis["checks"].append({"check": "recording_evidence", "passed": None,
                                           "detail": "message callback or download is incomplete"})
                analysis["passed"] = summarize_checks(c["passed"] for c in analysis["checks"])
            analysis["graded"] = not (incomplete and needs_media)
            if call.get("run_id"):
                analysis["run_id"] = call["run_id"]
            if "mailbox_transcript" in work:
                call["mailbox_transcript"] = work["mailbox_transcript"]
            analysis["finalization"] = {"state": "complete", "media_signature": signature,
                                        "runtime": snapshot(call)}
            call["analysis"] = analysis
            if runtime._call_store().upsert_incoming_call(call_id=sid, analysis=analysis) is None:
                return
            out_dir = runtime._call_results_dir(sid)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "call.json").write_text(json.dumps(call, indent=2, default=str))
            runtime._finish_simulation_run(call, analysis)
            return
    except Exception as exc:
        if not runtime._call_is_deleted(sid):
            analysis = {"graded": False, "passed": None, "run_id": call.get("run_id"),
                        "finalization": {"state": "error", "error_type": type(exc).__name__,
                                         "runtime": snapshot(call)}}
            call["analysis"] = analysis
            runtime._call_store().upsert_incoming_call(call_id=sid, analysis=analysis)
            runtime._finish_simulation_run(call, analysis)


async def startup(_app: web.Application) -> None:
    for saved in runtime._call_store().list_calls_awaiting_finalization():
        call = restore(saved.id)
        if call is None:
            continue
        for recording in saved.recordings:
            if recording.status != "absent":
                url = runtime._recording_media_url(recording.id)
                if url:
                    start_download(call, url, recording.id, recording.kind)
        schedule(call)


async def cleanup(_app: web.Application) -> None:
    tasks = [*runtime.STATE.finalization_tasks.values(),
             *(task for downloads in runtime.STATE.recording_tasks.values() for task in downloads.values())]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    runtime.STATE.finalization_tasks.clear()
    runtime.STATE.recording_tasks.clear()
