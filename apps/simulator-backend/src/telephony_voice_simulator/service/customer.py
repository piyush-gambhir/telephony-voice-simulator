"""Synthetic customer: configured personas and the sessions it has held."""

from __future__ import annotations

import json
import os
from typing import Any

from ..storage.sqlite import SimulatorStore
from ..telephony.twilio import customer
from .errors import ValidationError

REQUIRED_ENV = (
    "PSTN_CUSTOMER_PROFILES",
    "PSTN_CUSTOMER_ALLOWED_CALLERS",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "TWILIO_ACCOUNT_SID",
    "PUBLIC_BASE_URL",
)
# Scanned before the limit applies, so sessions are not crowded out by ordinary calls.
SCAN_LIMIT = 1000


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


def list_sessions(store: SimulatorStore, *, limit: int = 20) -> list[dict[str, Any]]:
    """Calls that carry a customer dialogue, newest first, with the dialogue."""
    if limit < 1:
        raise ValidationError("limit must be at least 1")
    sessions = []
    for call in store.list_incoming_calls(limit=SCAN_LIMIT):
        if "customer_dialogue" not in call.analysis:
            continue
        sessions.append({
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
            "dialogue": call.analysis["customer_dialogue"],
        })
        if len(sessions) == limit:
            break
    return sessions
