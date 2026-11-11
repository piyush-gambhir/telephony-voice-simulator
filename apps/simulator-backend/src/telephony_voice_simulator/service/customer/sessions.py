"""Read customer sessions and merge transcripts without overwriting concurrent grades."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from ...grading import personas as judge
from ...integrations import elevenlabs
from ...storage.sqlite import SimulatorStore
from ..errors import NotFoundError, ValidationError
from .common import _translated

# Scan before applying the result limit, because ordinary calls share the repository.
SCAN_LIMIT = 1000
# ElevenLabs calls the persona "agent" and the voice agent under test "user".
ROLES = {"user": "user", "agent": "assistant"}
# Refreshes, webhook deliveries and grade writes for one call share this lock.
_REFRESH_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


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
        "agent": {k: (call.analysis.get("elevenlabs") or {}).get(k)
                  for k in ("agent_id", "branch_id", "version_id")},
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


def _dialogue(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dialogue = []
    for turn in transcript or []:
        role = ROLES.get(turn.get("role"))
        text = turn.get("message") or ""
        if role == "assistant":
            text = judge.strip_tags(text)
        if role and text:
            turn_ = {"role": role, "content": text}
            if isinstance(turn.get("time_in_call_secs"), (int, float)):
                turn_["t"] = turn["time_in_call_secs"]  # seconds into the call, for timings
            dialogue.append(turn_)
    return dialogue


def _save_transcript(
    store: SimulatorStore, call: Any, conversation_id: str, detail: dict[str, Any]
) -> Any:
    meta = dict(call.analysis.get("elevenlabs") or {})
    meta.update({
        "conversation_id": conversation_id,
        "status": detail.get("status"),
        "termination_reason": (detail.get("metadata") or {}).get("termination_reason"),
        # The exact persona agent version that held the call.
        "branch_id": detail.get("branch_id") or meta.get("branch_id"),
        "version_id": detail.get("version_id"),
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
    with _REFRESH_LOCKS[call_id]:
        # Read inside the lock, so a grade saved meanwhile is kept rather than overwritten.
        call = store.get_incoming_call(call_id)
        if call is None or "customer_dialogue" not in call.analysis:
            return None
        saved = _save_transcript(store, call, data["conversation_id"], data)
        persona = saved.analysis.get("persona") or {}
        gradable = persona.get("expectations") or any((persona.get("checks") or {}).values())
        # Duplicate deliveries must not start a second paid grading.
        claim = bool(gradable) and not saved.analysis.get("graded") \
            and not saved.analysis.get("grading")
        if claim:
            store.upsert_incoming_call(
                call_id=call_id, analysis={**saved.analysis, "grading": True}
            )
    return {"call_id": call_id, "grade_pending": claim}


def _update_analysis(store: SimulatorStore, call_id: str, changes: dict[str, Any]) -> None:
    """Merge ``changes`` into the call's current analysis, under the call's lock."""
    with _REFRESH_LOCKS[call_id]:
        call = store.get_incoming_call(call_id)
        if call is None or store.upsert_incoming_call(
            call_id=call_id, analysis={**call.analysis, **changes}
        ) is None:
            raise NotFoundError(f"Unknown customer session: {call_id}")
