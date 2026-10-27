"""Synthetic customer: personas and their queue, the ElevenLabs persona agents, and grading."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from .. import personas
from ..grading import personas as judge
from ..integrations import elevenlabs
from ..storage.sqlite import SimulatorStore
from ..telephony.twilio import customer
from .errors import ConflictError, NotFoundError, ValidationError

log = logging.getLogger(__name__)

REQUIRED_ENV = (
    "PSTN_CUSTOMER_PROFILES",
    "PSTN_CUSTOMER_ALLOWED_CALLERS",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_CUSTOMER_AGENT_ID",
    "TWILIO_ACCOUNT_SID",
)
OPTIONAL_ENV = (
    "ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID",
    "ELEVENLABS_WEBHOOK_SECRET",
    "PSTN_CUSTOMER_PERSONAS_DIR",
)
# Scanned before the limit applies, so sessions are not crowded out by ordinary calls.
SCAN_LIMIT = 1000
# ElevenLabs roles: "user" is the agent under test, "agent" is our persona.
ROLES = {"user": "user", "agent": "assistant"}
# Overlapping refreshes of one call run one at a time, so the last write is the newest read.
_REFRESH_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
# Judge tests are reusable slots rewritten for every grading, so gradings run one at a time.
_JUDGE_LOCK = threading.Lock()
JUDGE_SLOT_NAME = "TVS judge slot {} (telephony-voice-simulator)"
FIDELITY_NAME = "TVS persona {} (telephony-voice-simulator)"
POLL_SECONDS = 3.0
JUDGE_TIMEOUT_SECONDS = 300.0
FIDELITY_TIMEOUT_SECONDS = 600.0
TRANSCRIPT_TIMEOUT_SECONDS = 300.0
MAX_REPEAT = 10

Progress = Callable[[str], None]


def _quiet(_line: str) -> None:
    pass


def _translated(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except elevenlabs.ElevenLabsError as exc:
        raise ConflictError(str(exc)) from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _lines() -> dict[str, str]:
    try:
        lines = customer.profiles()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read PSTN_CUSTOMER_PROFILES: {exc}") from exc
    if not isinstance(lines, dict):
        raise ValidationError("PSTN_CUSTOMER_PROFILES must contain a JSON object")
    return lines


def _catalog() -> dict[str, dict[str, Any]]:
    return _translated(personas.catalog)


def profiles() -> dict[str, Any]:
    """Persona lines and their default persona (a catalog id or free profile text), plus
    which environment keys are set (never their values)."""
    return {
        "profiles": _lines(),
        "allowed_callers": [
            n for n in os.environ.get("PSTN_CUSTOMER_ALLOWED_CALLERS", "").split(",") if n
        ],
        "configured": {key: bool(os.environ.get(key)) for key in REQUIRED_ENV + OPTIONAL_ENV},
    }


def list_personas() -> list[dict[str, Any]]:
    return list(_catalog().values())


def get_persona(persona_id: str) -> dict[str, Any]:
    catalog = _catalog()
    if persona_id not in catalog:
        raise NotFoundError(f"Unknown persona: {persona_id}")
    return catalog[persona_id]


def resolve_persona(persona_id: str | None = None, inline: Any = None) -> dict[str, Any]:
    """A catalog persona by id, or an inline one (a mapping, or YAML/JSON text)."""
    if (persona_id is None) == (inline is None):
        raise ValidationError("Give either a persona id or an inline persona")
    if persona_id is not None:
        return get_persona(persona_id)
    parse = personas.parse if isinstance(inline, str) else personas.validate
    return _translated(lambda: parse(inline))


def _check_engine(persona: dict[str, Any]) -> None:
    _translated(lambda: elevenlabs.customer_agent_id(persona["engine"]))


def queue_persona(
    store: SimulatorStore,
    number: str,
    *,
    persona_id: str | None = None,
    inline: Any = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Queue a persona for the next call on a persona line; a copy is queued, not a reference."""
    if number not in _lines():
        raise ValidationError(f"{number} is not a persona line in PSTN_CUSTOMER_PROFILES")
    persona = resolve_persona(persona_id, inline)
    _check_engine(persona)
    return store.enqueue_customer_persona(number, persona, label)


def list_queue(store: SimulatorStore, number: str | None = None) -> list[dict[str, Any]]:
    return store.list_customer_persona_queue(number)


def cancel_queued(store: SimulatorStore, queue_id: str) -> dict[str, Any]:
    if not store.cancel_customer_persona(queue_id):
        raise NotFoundError(f"Nothing queued with id {queue_id}")
    return {"cancelled": queue_id}


def _configured_engines() -> list[str]:
    return [e for e, key in elevenlabs.AGENT_ENV.items() if os.environ.get(key)]


def sync_agent() -> dict[str, Any]:
    """Push prompt, audio format, call limit, model and override permissions to each agent."""
    engines = _configured_engines()
    if "chained" not in engines:
        _translated(elevenlabs.customer_agent_id)
    applied = {}
    for engine in engines:
        agent_id = elevenlabs.customer_agent_id(engine)
        settings = customer.agent_settings(engine)
        _translated(lambda a=agent_id, s=settings: elevenlabs.update_agent(a, s))
        applied[engine] = {"agent_id": agent_id, "applied": settings}
    return applied


def _session(call: Any) -> dict[str, Any]:
    persona = call.analysis.get("persona") or {}
    return {
        "call_id": call.id,
        "direction": call.direction,
        "from": call.from_address,
        "to": call.to_address,
        "status": call.status,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "duration_s": call.duration_s,
        "persona_id": persona.get("id"),
        "engine": persona.get("engine"),
        "persona_source": call.analysis.get("persona_source"),
        "stopped": call.analysis.get("stopped"),
        "error_type": call.analysis.get("error_type"),
        "transcript_status": (call.analysis.get("elevenlabs") or {}).get("status"),
        "dialogue": call.analysis["customer_dialogue"],
        "grade": call.analysis.get("grade"),
    }


def list_sessions(store: SimulatorStore, *, limit: int = 20) -> list[dict[str, Any]]:
    """Calls that carry a customer dialogue, newest first, with the dialogue."""
    if limit < 1:
        raise ValidationError("limit must be at least 1")
    sessions = []
    for call in store.list_incoming_calls(limit=SCAN_LIMIT):
        if "customer_dialogue" not in call.analysis:
            continue
        sessions.append(_session(call))
        if len(sessions) == limit:
            break
    return sessions


def get_session(store: SimulatorStore, call_id: str) -> dict[str, Any]:
    return _session(_customer_call(store, call_id))


def _customer_call(store: SimulatorStore, call_id: str) -> Any:
    call = store.get_incoming_call(call_id)
    if call is None or "customer_dialogue" not in call.analysis:
        raise NotFoundError(f"Unknown customer session: {call_id}")
    return call


def _dialogue(transcript: list[dict[str, Any]]) -> list[dict[str, str]]:
    dialogue = []
    for turn in transcript or []:
        role = ROLES.get(turn.get("role"))
        text = turn.get("message") or ""
        if role == "assistant":
            text = judge.strip_tags(text)
        if role and text:
            dialogue.append({"role": role, "content": text})
    return dialogue


def _save_transcript(
    store: SimulatorStore, call: Any, conversation_id: str, detail: dict[str, Any]
) -> Any:
    meta = dict(call.analysis.get("elevenlabs") or {})
    meta.update({
        "conversation_id": conversation_id,
        "status": detail.get("status"),
        "termination_reason": (detail.get("metadata") or {}).get("termination_reason"),
    })
    saved = store.upsert_incoming_call(
        call_id=call.id,
        analysis={
            **call.analysis,
            "customer_dialogue": _dialogue(detail.get("transcript")),
            "elevenlabs": meta,
        },
    )
    if saved is None:
        raise NotFoundError(f"Unknown customer session: {call.id}")
    return saved


def refresh_session(store: SimulatorStore, call_id: str) -> dict[str, Any]:
    """Fetch the call's transcript from ElevenLabs and store it as the session dialogue.

    ``transcript_status`` stays "registered" until ElevenLabs has logged the conversation,
    then follows it ("in-progress", "processing", "done", "failed").
    """
    with _REFRESH_LOCKS[call_id]:
        return _session(_refresh(store, call_id))


def _refresh(store: SimulatorStore, call_id: str) -> Any:
    call = _customer_call(store, call_id)
    meta = call.analysis.get("elevenlabs") or {}
    if meta.get("status") == "done":
        # A finished transcript never changes; keep it rather than refetching.
        return call
    agent_id = meta.get("agent_id") or _translated(elevenlabs.customer_agent_id)
    summary = _translated(lambda: elevenlabs.find_conversation(agent_id, call_id))
    if summary is None:
        return call
    detail = _translated(lambda: elevenlabs.get_conversation(summary["conversation_id"]))
    return _save_transcript(store, call, summary["conversation_id"], detail)


def store_conversation(store: SimulatorStore, data: dict[str, Any]) -> dict[str, Any] | None:
    """Save a post-call webhook's transcript on its call; None when the call is not ours."""
    client_data = data.get("conversation_initiation_client_data") or {}
    call_id = data.get("user_id") or client_data.get("user_id")
    if not isinstance(call_id, str) or not data.get("conversation_id"):
        return None
    call = store.get_incoming_call(call_id)
    if call is None or "customer_dialogue" not in call.analysis:
        return None
    with _REFRESH_LOCKS[call_id]:
        saved = _save_transcript(store, call, data["conversation_id"], data)
    persona = saved.analysis.get("persona") or {}
    gradable = persona.get("expectations") or any((persona.get("checks") or {}).values())
    return {"call_id": call_id, "grade_pending": bool(gradable) and not saved.analysis.get("graded")}


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
    agent_id = _translated(lambda: elevenlabs.customer_agent_id(persona["engine"]))
    body = judge.fidelity_test(
        FIDELITY_NAME.format(persona["id"]), persona, elevenlabs.evaluation_model()
    )
    test_id = _translated(lambda: elevenlabs.save_test(body))
    invocation_id = _translated(lambda: elevenlabs.run_tests(agent_id, [test_id], repeat=repeat))
    progress(f"started {repeat} run(s) of {body['name']}")
    runs = judge.run_results(_wait_for_invocation(invocation_id, FIDELITY_TIMEOUT_SECONDS, progress))
    return {
        "persona_id": persona["id"],
        "engine": persona["engine"],
        "agent_id": agent_id,
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
    checks = judge.run_checks(persona, dialogue)
    expectations: list[dict[str, Any]] = []
    grade: dict[str, Any] = {"persona_id": persona["id"], "checks": checks}
    if persona["expectations"]:
        expectations, invocation = _judge(call, persona, dialogue, progress)
        grade.update(invocation)
    grade.update({
        "expectations": expectations,
        "passed": all(c["passed"] for c in checks) and all(e["passed"] for e in expectations),
        "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    saved = store.upsert_incoming_call(
        call_id=call_id, analysis={**call.analysis, "grade": grade, "graded": True}
    )
    if saved is None:
        raise NotFoundError(f"Unknown customer session: {call_id}")
    return grade


def _judge(
    call: Any, persona: dict[str, Any], dialogue: list[dict[str, str]], progress: Progress
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    model = elevenlabs.evaluation_model()
    agent_id = (call.analysis.get("elevenlabs") or {}).get("agent_id")
    agent_id = agent_id or _translated(lambda: elevenlabs.customer_agent_id(persona["engine"]))
    with _JUDGE_LOCK:
        test_ids = []
        for slot, expectation in enumerate(persona["expectations"], start=1):
            body = judge.judge_test(
                JUDGE_SLOT_NAME.format(slot), persona, dialogue, expectation, model
            )
            test_ids.append(_translated(lambda body=body: elevenlabs.save_test(body)))
        invocation_id = _translated(lambda: elevenlabs.run_tests(agent_id, test_ids))
        progress(f"judging {len(test_ids)} expectation(s) ({invocation_id})")
        invocation = _wait_for_invocation(invocation_id, JUDGE_TIMEOUT_SECONDS, progress)
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
        call = store.get_incoming_call(call_id)
        if call is not None:
            store.upsert_incoming_call(
                call_id=call_id, analysis={**call.analysis, "grade_error": str(exc)}
            )
