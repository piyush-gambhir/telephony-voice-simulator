"""Customer personas: who the synthetic customer is, and what the agent under test must do.

A persona is a YAML (or JSON) mapping. The bundled catalog lives next to this module;
``PSTN_CUSTOMER_PERSONAS_DIR`` adds a private catalog whose IDs override bundled ones.
Coding agents can also pass a persona inline when they queue a call. Either way, the
validated persona is copied onto the call, so later catalog edits never change a result.

Fields:
  id             required, lowercase letters, digits and dashes
  profile        required, synthetic facts and behavior; fills ``{{customer_profile}}``
  description    optional, for people
  engine         "chained" (default) or "realtime"; picks the ElevenLabs agent
  first_message  optional, what the customer says on answering, e.g. "Hello?"
  language       optional ISO code, e.g. "en"
  voice_id       optional ElevenLabs voice
  expectations   what the agent under test must do; each is judged on its own
  checks         deterministic transcript checks: must_say, must_not_say (case-insensitive
                 substrings of the agent under test's turns), max_turns
  fidelity       persona self-test: scenario (the simulated dealership's script) and
                 conditions (extra rules the customer must follow)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).parent / "catalog"
ENGINES = ("chained", "realtime")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_TEXT_FIELDS = ("description", "first_message", "language", "voice_id")
_FIELDS = {"id", "profile", "engine", "expectations", "checks", "fidelity", *_TEXT_FIELDS}
MAX_EXPECTATIONS = 10


class PersonaError(ValueError):
    """A persona file or inline persona is invalid."""


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise PersonaError(f"{field} must be a list of non-empty strings")
    return [v.strip() for v in value]


def validate(raw: Any) -> dict[str, Any]:
    """The persona in canonical form, with defaults filled and unknown keys rejected."""
    if not isinstance(raw, dict):
        raise PersonaError("A persona must be a mapping")
    if unknown := set(raw) - _FIELDS:
        raise PersonaError(f"Unknown persona fields: {', '.join(sorted(unknown))}")
    persona_id = raw.get("id")
    if not isinstance(persona_id, str) or not _ID.fullmatch(persona_id):
        raise PersonaError("id must be lowercase letters, digits and dashes")
    profile = raw.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise PersonaError(f"{persona_id}: profile is required")
    engine = raw.get("engine") or "chained"
    if engine not in ENGINES:
        raise PersonaError(f"{persona_id}: engine must be one of {', '.join(ENGINES)}")
    persona: dict[str, Any] = {"id": persona_id, "profile": profile.strip(), "engine": engine}
    for field in _TEXT_FIELDS:
        value = raw.get(field)
        if value is not None:
            if not isinstance(value, str):
                raise PersonaError(f"{persona_id}: {field} must be text")
            persona[field] = value.strip()
    persona["expectations"] = _strings(raw.get("expectations"), f"{persona_id}: expectations")
    if len(persona["expectations"]) > MAX_EXPECTATIONS:
        raise PersonaError(f"{persona_id}: at most {MAX_EXPECTATIONS} expectations")
    checks = raw.get("checks") or {}
    if not isinstance(checks, dict) or set(checks) - {"must_say", "must_not_say", "max_turns"}:
        raise PersonaError(f"{persona_id}: checks takes must_say, must_not_say and max_turns")
    max_turns = checks.get("max_turns")
    if max_turns is not None and (not isinstance(max_turns, int) or max_turns < 1):
        raise PersonaError(f"{persona_id}: checks.max_turns must be a positive whole number")
    persona["checks"] = {
        "must_say": _strings(checks.get("must_say"), f"{persona_id}: checks.must_say"),
        "must_not_say": _strings(checks.get("must_not_say"), f"{persona_id}: checks.must_not_say"),
        "max_turns": max_turns,
    }
    fidelity = raw.get("fidelity") or {}
    if not isinstance(fidelity, dict) or set(fidelity) - {"scenario", "conditions"}:
        raise PersonaError(f"{persona_id}: fidelity takes scenario and conditions")
    scenario = fidelity.get("scenario")
    if scenario is not None and (not isinstance(scenario, str) or not scenario.strip()):
        raise PersonaError(f"{persona_id}: fidelity.scenario must be text")
    persona["fidelity"] = {
        "scenario": scenario.strip() if scenario else None,
        "conditions": _strings(fidelity.get("conditions"), f"{persona_id}: fidelity.conditions"),
    }
    return persona


def parse(text: str) -> dict[str, Any]:
    """An inline persona from YAML or JSON text."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PersonaError(f"Persona is not valid YAML or JSON: {exc}") from exc
    return validate(raw)


def _load_dir(path: Path) -> dict[str, dict[str, Any]]:
    personas = {}
    for file in sorted([*path.glob("*.yaml"), *path.glob("*.yml"), *path.glob("*.json")]):
        try:
            persona = parse(file.read_text())
        except PersonaError as exc:
            raise PersonaError(f"{file.name}: {exc}") from exc
        if persona["id"] in personas:
            raise PersonaError(f"{file.name}: duplicate persona id {persona['id']}")
        personas[persona["id"]] = persona
    return personas


def catalog() -> dict[str, dict[str, Any]]:
    """Bundled personas, overridden by ``PSTN_CUSTOMER_PERSONAS_DIR`` ones with the same id."""
    personas = _load_dir(CATALOG_DIR)
    private = os.environ.get("PSTN_CUSTOMER_PERSONAS_DIR", "")
    if private:
        directory = Path(private)
        if not directory.is_dir():
            raise PersonaError(f"PSTN_CUSTOMER_PERSONAS_DIR is not a directory: {private}")
        personas.update(_load_dir(directory))
    return personas


def get(persona_id: str) -> dict[str, Any]:
    personas = catalog()
    if persona_id not in personas:
        raise PersonaError(f"Unknown persona: {persona_id}")
    return personas[persona_id]


def for_line(value: str) -> dict[str, Any]:
    """A line's default persona: a catalog id, or legacy free-text profile."""
    personas = catalog()
    if value in personas:
        return personas[value]
    return validate({"id": "line-profile", "profile": value})
