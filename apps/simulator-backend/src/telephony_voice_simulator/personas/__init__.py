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
  environment    what the dealership hears behind the customer: background (an ElevenLabs
                 preset, see BACKGROUNDS) and volume (0.01 to 1, default 0.15)
  turn           how the customer takes turns: eagerness ("patient", "normal", "eager")
                 and timeout_s (seconds of silence before the customer speaks up, 1 to 30)

ElevenLabs sets environment and turn per agent, not per call, so a persona that uses them
runs on a variant agent (see ``variant``) that ``customer sync-agent`` creates and keeps.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).parent / "catalog"
ENGINES = ("chained", "realtime")
BACKGROUNDS = ("office1", "office2", "restaurant", "city", "typing",
               "elevator1", "elevator2", "elevator3", "elevator4")
EAGERNESS = ("patient", "normal", "eager")
DEFAULT_VOLUME = 0.15
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_TEXT_FIELDS = ("description", "first_message", "language", "voice_id")
_FIELDS = {"id", "profile", "engine", "expectations", "checks", "fidelity", "environment",
           "turn", *_TEXT_FIELDS}
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
    persona["environment"] = _environment(persona_id, raw.get("environment"))
    persona["turn"] = _turn(persona_id, raw.get("turn"))
    return persona


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _environment(persona_id: str, raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"background", "volume"}:
        raise PersonaError(f"{persona_id}: environment takes background and volume")
    if raw.get("background") not in BACKGROUNDS:
        raise PersonaError(f"{persona_id}: environment.background must be one of "
                           f"{', '.join(BACKGROUNDS)}")
    volume = raw.get("volume", DEFAULT_VOLUME)
    if not _number(volume) or not 0.01 <= volume <= 1:
        raise PersonaError(f"{persona_id}: environment.volume must be between 0.01 and 1")
    return {"background": raw["background"], "volume": round(float(volume), 2)}


def _turn(persona_id: str, raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw or set(raw) - {"eagerness", "timeout_s"}:
        raise PersonaError(f"{persona_id}: turn takes eagerness and timeout_s")
    eagerness, timeout = raw.get("eagerness"), raw.get("timeout_s")
    if eagerness is not None and eagerness not in EAGERNESS:
        raise PersonaError(f"{persona_id}: turn.eagerness must be one of {', '.join(EAGERNESS)}")
    if timeout is not None and (not _number(timeout) or not 1 <= timeout <= 30):
        raise PersonaError(f"{persona_id}: turn.timeout_s must be between 1 and 30")
    return {"eagerness": eagerness, "timeout_s": float(timeout) if timeout is not None else None}


def variant(persona: dict[str, Any]) -> dict[str, Any] | None:
    """The agent-level settings this persona needs beyond its engine's base agent, if any."""
    if not persona.get("environment") and not persona.get("turn"):
        return None
    return {"engine": persona["engine"], "environment": persona.get("environment"),
            "turn": persona.get("turn")}


def variant_name(settings: dict[str, Any]) -> str:
    """A stable, readable ElevenLabs agent name for a variant, e.g. "chained city 0.2 eager"."""
    parts = [settings["engine"]]
    if environment := settings["environment"]:
        parts += [environment["background"], f"{environment['volume']:g}"]
    if turn := settings["turn"]:
        parts += [turn["eagerness"] or "normal"]
        if turn["timeout_s"] is not None:
            parts.append(f"wait {turn['timeout_s']:g}s")
    return f"TVS persona variant: {' '.join(parts)} (telephony-voice-simulator)"


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
