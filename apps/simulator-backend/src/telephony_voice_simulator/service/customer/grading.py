"""Run persona fidelity tests and grade conversations against their expectations."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ...grading import personas as judge
from ...grading import scribe
from ...integrations import elevenlabs
from ...storage.sqlite import SimulatorStore
from ..errors import ConflictError, NotFoundError, ValidationError
from .agents import resolve_agent
from .common import Progress, _quiet, _translated
from .sessions import _REFRESH_LOCKS, _refresh, _update_analysis

log = logging.getLogger(__name__)

# Recordings arrive after the transcript; give downloads time to finish.
SCRIBE_WAIT_SECONDS = 90
# Judge tests reuse named slots, so serialize their updates within this process.
_JUDGE_LOCK = threading.Lock()
JUDGE_SLOT_NAME = "TVS judge slot {} (telephony-voice-simulator)"
FIDELITY_NAME = "TVS persona {} (telephony-voice-simulator)"
POLL_SECONDS = 3.0
JUDGE_TIMEOUT_SECONDS = 300.0
FIDELITY_TIMEOUT_SECONDS = 600.0
TRANSCRIPT_TIMEOUT_SECONDS = 300.0
MAX_REPEAT = 10


def _wait_for_invocation(invocation_id: str, timeout: float, progress: Progress) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        invocation = _translated(lambda: elevenlabs.get_invocation(invocation_id))
        if judge.finished(invocation):
            return invocation
        if time.monotonic() > deadline:
            raise ConflictError(
                f"ElevenLabs test invocation {invocation_id} did not finish in {int(timeout)}s"
            )
        progress(f"waiting for ElevenLabs tests ({invocation_id})")
        time.sleep(POLL_SECONDS)


def check_persona(
    persona: dict[str, Any], *, repeat: int = 1, progress: Progress = _quiet
) -> dict[str, Any]:
    """Run the persona's fidelity test on its engine's agent: does the customer stay in role?"""
    if not 1 <= repeat <= MAX_REPEAT:
        raise ValidationError(f"repeat must be between 1 and {MAX_REPEAT}")
    agent_id, branch_id = resolve_agent(persona)
    body = judge.fidelity_test(
        FIDELITY_NAME.format(persona["id"]), persona, elevenlabs.evaluation_model()
    )
    test_id = _translated(lambda: elevenlabs.save_test(body))
    invocation_id = _translated(lambda: elevenlabs.run_tests(
        agent_id, [test_id], repeat=repeat, branch_id=branch_id
    ))
    progress(f"started {repeat} run(s) of {body['name']}")
    invocation = _wait_for_invocation(invocation_id, FIDELITY_TIMEOUT_SECONDS, progress)
    runs = judge.run_results(invocation)
    return {
        "persona_id": persona["id"],
        "engine": persona["engine"],
        "agent_id": agent_id,
        "branch_id": invocation.get("branch_id"),
        "version_id": invocation.get("version_id"),
        "test_id": test_id,
        "invocation_id": invocation_id,
        "evaluation_model": body["evaluation_model"],
        "passed": sum(run["passed"] for run in runs),
        "runs": runs,
        "credits": sum(run["credits"] or 0 for run in runs),
    }


def _wait_for_transcript(store: SimulatorStore, call_id: str, progress: Progress) -> Any:
    deadline = time.monotonic() + TRANSCRIPT_TIMEOUT_SECONDS
    while True:
        with _REFRESH_LOCKS[call_id]:
            call = _refresh(store, call_id)
        status = (call.analysis.get("elevenlabs") or {}).get("status")
        if status in {"done", "failed"} or call.analysis.get("stopped"):
            return call
        if time.monotonic() > deadline:
            raise ConflictError(f"The transcript for {call_id} is not ready (status {status})")
        progress(f"waiting for the transcript ({status})")
        time.sleep(POLL_SECONDS)


def grade_session(
    store: SimulatorStore, call_id: str, *, wait: bool = False, progress: Progress = _quiet
) -> dict[str, Any]:
    """Grade a finished call against its persona: deterministic checks, then one judge test
    per expectation. With ``wait``, first wait for ElevenLabs to finish the transcript."""
    if wait:
        call = _wait_for_transcript(store, call_id, progress)
    else:
        with _REFRESH_LOCKS[call_id]:
            call = _refresh(store, call_id)
    persona = call.analysis.get("persona")
    if not persona:
        raise ValidationError(f"{call_id} has no persona to grade against")
    status = (call.analysis.get("elevenlabs") or {}).get("status")
    if status != "done":
        raise ConflictError(f"The transcript for {call_id} is not finished (status {status})")
    dialogue = call.analysis["customer_dialogue"]
    heard = _scribe(store, call, progress)
    checks = judge.run_checks(persona, dialogue, (heard or {}).get("remote_text"))
    expectations: list[dict[str, Any]] = []
    meta = call.analysis.get("elevenlabs") or {}
    grade: dict[str, Any] = {
        "persona_id": persona["id"],
        # The persona agent version that held the call, so drifting personas show up.
        "agent": {k: meta.get(k) for k in ("agent_id", "branch_id", "version_id")},
        "checks": checks,
    }
    if persona["expectations"]:
        expectations, invocation = _judge(call, persona, dialogue, progress)
        grade.update(invocation)
    grade.update({
        "expectations": expectations,
        "passed": all(c["passed"] for c in checks) and all(e["passed"] for e in expectations),
        "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _update_analysis(store, call_id, {"grade": grade, "graded": True, "grading": False,
                                      "grade_error": None})
    return grade


def _scribe(store: SimulatorStore, call: Any, progress: Progress) -> dict[str, Any] | None:
    """The call recording transcribed by Scribe, saved on the call; None without a recording.

    The recording lands a little after the call ends, so a recorded call waits for it.
    """
    if "scribe" in call.analysis:
        return call.analysis["scribe"]
    # Only a call whose recording started (or already landed) has one to wait for.
    if call.recording_status not in ("recording", "completed") and not call.recordings:
        return None
    deadline = time.monotonic() + SCRIBE_WAIT_SECONDS
    while True:
        latest = store.get_incoming_call(call.id) or call
        path = next((r.local_path for r in latest.recordings
                     if r.kind == "full_call" and r.status == "completed" and r.local_path), None)
        if path or time.monotonic() > deadline:
            break
        progress("waiting for the call recording")
        time.sleep(5)
    channels = scribe.transcribe(Path(path)) if path else None
    if channels is None:
        return None
    summary = scribe.summarize(channels, offset=float(call.analysis.get("recording_offset_s") or 0))
    _update_analysis(store, call.id, {"scribe": summary})
    return summary


def _judge(
    call: Any, persona: dict[str, Any], dialogue: list[dict[str, str]], progress: Progress
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model = elevenlabs.evaluation_model()
    meta = call.analysis.get("elevenlabs") or {}
    agent_id, branch_id = meta.get("agent_id"), meta.get("branch_id")
    if not agent_id:
        agent_id, branch_id = resolve_agent(persona)
    with _JUDGE_LOCK:
        test_ids: list[str] = []
        bodies: list[dict[str, Any]] = []
        for slot, expectation in enumerate(persona["expectations"], start=1):
            body = _translated(lambda slot=slot, expectation=expectation: judge.judge_test(
                JUDGE_SLOT_NAME.format(slot), persona, dialogue, expectation, model
            ))
            bodies.append(body)
            test_ids.append(_translated(lambda body=body: elevenlabs.save_test(body)))
        invocation_id = _translated(
            lambda: elevenlabs.run_tests(agent_id, test_ids, branch_id=branch_id)
        )
        progress(f"judging {len(test_ids)} expectation(s) ({invocation_id})")
        invocation = _wait_for_invocation(invocation_id, JUDGE_TIMEOUT_SECONDS, progress)
    # Slots are shared by every process grading in this workspace; the lock only covers
    # this one. Each run records the test as it ran, so check it judged our transcript.
    if not judge.ran_as_saved(invocation, dict(zip(test_ids, bodies, strict=True))):
        raise ConflictError("Another grading rewrote the judge tests mid-run; grade again")
    by_test = {run["test_id"]: run for run in judge.run_results(invocation)}
    results = [
        {
            "expectation": expectation,
            "passed": by_test.get(test_id, {}).get("passed", False),
            "reasons": by_test.get(test_id, {}).get("reasons", ["No result from ElevenLabs"]),
        }
        for expectation, test_id in zip(persona["expectations"], test_ids, strict=True)
    ]
    credits = sum(run["credits"] or 0 for run in by_test.values())
    return results, {"invocation_id": invocation_id, "evaluation_model": model, "credits": credits}


def grade_quietly(store: SimulatorStore, call_id: str) -> None:
    """Grade from the post-call webhook; failures are recorded on the call, not raised."""
    try:
        grade_session(store, call_id)
    except (ConflictError, NotFoundError, ValidationError) as exc:
        log.warning("Grading %s failed: %s", call_id, exc)
        try:
            _update_analysis(store, call_id, {"grade_error": str(exc), "grading": False})
        except NotFoundError:
            pass
