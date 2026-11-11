"""Validate customer profiles, persona selection and engine configuration."""

from __future__ import annotations

import json
import os
from typing import Any

from ... import personas
from ...integrations import elevenlabs
from ...telephony.twilio import customer
from ..errors import NotFoundError, ValidationError
from .common import _translated

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
