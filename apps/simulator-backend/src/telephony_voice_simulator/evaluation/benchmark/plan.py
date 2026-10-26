"""Plan resolution: the scenario catalog, suite YAML and flag overrides into one run plan."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from ...domains.amd.validation import validate_scenario
from ...paths import SCENARIOS_DIR
from ...service.errors import ValidationError


def load_scenarios() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        raw = validate_scenario(yaml.safe_load(path.read_text()), source=str(path))
        name = raw.get("name")
        if name:
            raw["_file"] = path.name
            catalog[name] = raw
    return catalog


def resolve_plan(
    *,
    suite: str | None = None,
    scenarios: list[str] | None = None,
    repeat: int | None = None,
    numbers: list[str] | None = None,
    concurrency: int | None = None,
    pace: float | None = None,
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge a suite file with explicit overrides; the overrides win."""
    suite_file: dict[str, Any] = {}
    if suite:
        loaded = yaml.safe_load(Path(suite).expanduser().read_text())
        suite_file = {} if loaded is None else loaded
    if not isinstance(suite_file, dict):
        raise ValidationError("benchmark suite must be a YAML mapping")
    if type(suite_file.get("version", 1)) is not int or suite_file.get("version", 1) != 1:
        raise ValidationError("unsupported benchmark suite version; expected 1")

    selected = scenarios or suite_file.get("scenarios") or "all"
    if isinstance(selected, str):
        selected = list(catalog) if selected == "all" else [selected]
    if not isinstance(selected, list) or any(not isinstance(n, str) for n in selected):
        raise ValidationError("scenarios must be a list of names or 'all'")
    exclusions = suite_file.get("exclude") or []
    if not isinstance(exclusions, list) or any(not isinstance(n, str) for n in exclusions):
        raise ValidationError("exclude must be a list of scenario names")
    excluded = set(exclusions)
    names = [n for n in selected if n not in excluded]
    if len(names) != len(set(names)):
        raise ValidationError("scenario names must be unique; use repeat for multiple attempts")

    unknown = [n for n in names if n not in catalog]
    if unknown:
        raise ValidationError(f"unknown scenario(s): {', '.join(unknown)}")
    if not names:
        raise ValidationError("no scenarios selected")

    lines = list(numbers or []) or _suite_numbers(suite_file)
    if any(not isinstance(n, str) or not n.strip() for n in lines):
        raise ValidationError("simulator numbers must be nonempty strings")
    lines = [n.strip() for n in lines]
    if len(lines) != len(set(lines)):
        raise ValidationError("simulator numbers must be unique; duplicate lanes would race")
    default_name = Path(suite).stem if suite else "ad-hoc"
    attempts = repeat if repeat is not None else suite_file.get("repeat", 1)
    # One lane per line: the scenario queue is per-number, so two concurrent
    # calls to the same line would race for it and mislabel each other.
    lanes = (
        concurrency if concurrency is not None else suite_file.get("concurrency", len(lines) or 1)
    )
    for field, value in (("repeat", attempts), ("concurrency", lanes)):
        if type(value) is not int or value < 1:
            raise ValidationError(f"{field} must be a positive integer")
    spacing = pace if pace is not None else suite_file.get("pace_seconds", 15.0)
    timeout = suite_file.get("call_timeout_seconds", 300.0)
    for field, value in (("pace_seconds", spacing), ("call_timeout_seconds", timeout)):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValidationError(f"{field} must be a finite nonnegative number")
    if timeout == 0:
        raise ValidationError("call_timeout_seconds must be positive")
    return {
        "name": suite_file.get("name") or default_name,
        "scenarios": names,
        "repeat": attempts,
        "pace_seconds": float(spacing),
        "call_timeout_seconds": float(timeout),
        "sim_numbers": lines,
        "concurrency": max(1, min(lanes, len(lines) or 1)),
    }


def _suite_numbers(suite: dict[str, Any]) -> list[str]:
    numbers = suite.get("numbers") or suite.get("sim_numbers")
    if numbers is not None:
        if not isinstance(numbers, list):
            raise ValidationError("numbers must be a list of quoted phone numbers")
        return numbers
    single = suite.get("sim_number")
    return [single] if single else []


def build_jobs(plan: dict[str, Any]) -> list[tuple[str, int]]:
    """(scenario, attempt) pairs, attempt-major so a partial run stays balanced.

    Interleaving this way means an interrupted or aborted suite has covered
    every scenario once before any scenario has been covered twice.
    """
    return [
        (name, attempt) for attempt in range(1, plan["repeat"] + 1) for name in plan["scenarios"]
    ]
