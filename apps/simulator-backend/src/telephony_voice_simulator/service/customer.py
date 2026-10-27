"""Synthetic customer: personas, the ElevenLabs persona agent, and the sessions it has held."""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from typing import Any

from ..integrations import elevenlabs
from ..storage.sqlite import SimulatorStore
from ..telephony.twilio import customer
from .errors import ConflictError, NotFoundError, ValidationError

REQUIRED_ENV = (
    "PSTN_CUSTOMER_PROFILES",
    "PSTN_CUSTOMER_ALLOWED_CALLERS",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_CUSTOMER_AGENT_ID",
    "TWILIO_ACCOUNT_SID",
)
# Scanned before the limit applies, so sessions are not crowded out by ordinary calls.
SCAN_LIMIT = 1000
# ElevenLabs roles: "user" is the agent under test, "agent" is our persona.
ROLES = {"user": "user", "agent": "assistant"}
# Overlapping refreshes of one call run one at a time, so the last write is the newest read.
_REFRESH_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


def profiles() -> dict[str, Any]:
    """Personas by number, plus which required environment keys are set (never their values)."""
    try:
        personas = customer.profiles()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read PSTN_CUSTOMER_PROFILES: {exc}") from exc
    if not isinstance(personas, dict):
        raise ValidationError("PSTN_CUSTOMER_PROFILES must contain a JSON object")
    return {
        "profiles": personas,
        "allowed_callers": [
            n for n in os.environ.get("PSTN_CUSTOMER_ALLOWED_CALLERS", "").split(",") if n
        ],
        "configured": {key: bool(os.environ.get(key)) for key in REQUIRED_ENV},
    }


def _translated(call: Any) -> Any:
    try:
        return call()
    except elevenlabs.ElevenLabsError as exc:
        raise ConflictError(str(exc)) from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def sync_agent() -> dict[str, Any]:
    """Push the persona prompt, mu-law audio and call length limit to the ElevenLabs agent."""
    agent_id = _translated(elevenlabs.customer_agent_id)
    config = customer.agent_config()
    _translated(lambda: elevenlabs.update_agent(agent_id, config))
    return {"agent_id": agent_id, "applied": config}


def _session(call: Any) -> dict[str, Any]:
    return {
        "call_id": call.id,
        "direction": call.direction,
        "from": call.from_address,
        "to": call.to_address,
        "status": call.status,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "duration_s": call.duration_s,
        "stopped": call.analysis.get("stopped"),
        "error_type": call.analysis.get("error_type"),
        "transcript_status": (call.analysis.get("elevenlabs") or {}).get("status"),
        "dialogue": call.analysis["customer_dialogue"],
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


def refresh_session(store: SimulatorStore, call_id: str) -> dict[str, Any]:
    """Fetch the call's transcript from ElevenLabs and store it as the session dialogue.

    ``transcript_status`` stays "registered" until ElevenLabs has logged the conversation,
    then follows it ("in-progress", "processing", "done", "failed").
    """
    with _REFRESH_LOCKS[call_id]:
        return _refresh_session(store, call_id)


def _refresh_session(store: SimulatorStore, call_id: str) -> dict[str, Any]:
    call = store.get_incoming_call(call_id)
    if call is None or "customer_dialogue" not in call.analysis:
        raise NotFoundError(f"Unknown customer session: {call_id}")
    meta = dict(call.analysis.get("elevenlabs") or {})
    if meta.get("status") == "done":
        # A finished transcript never changes; keep it rather than refetching.
        return _session(call)
    agent_id = meta.get("agent_id") or _translated(elevenlabs.customer_agent_id)
    summary = _translated(lambda: elevenlabs.find_conversation(agent_id, call_id))
    if summary is None:
        return _session(call)
    detail = _translated(lambda: elevenlabs.get_conversation(summary["conversation_id"]))
    dialogue = [
        {"role": ROLES[turn["role"]], "content": turn["message"]}
        for turn in detail.get("transcript") or []
        if turn.get("role") in ROLES and turn.get("message")
    ]
    meta.update({
        "agent_id": agent_id,
        "conversation_id": summary["conversation_id"],
        "status": detail.get("status"),
        "termination_reason": (detail.get("metadata") or {}).get("termination_reason"),
    })
    saved = store.upsert_incoming_call(
        call_id=call_id,
        analysis={**call.analysis, "customer_dialogue": dialogue, "elevenlabs": meta},
    )
    if saved is None:
        raise NotFoundError(f"Unknown customer session: {call_id}")
    return _session(saved)
