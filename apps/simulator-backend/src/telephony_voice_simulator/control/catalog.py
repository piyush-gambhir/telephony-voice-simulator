"""Unified catalog for AMD YAML and built-in IVR/PBX scenario packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..domains import scenario_entries
from ..paths import SCENARIOS_DIR
from ..scenario_validation import validate_scenario


def _title(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _step(step: dict[str, Any], index: int) -> dict[str, Any]:
    if "wait" in step:
        return {"index": index, "kind": "wait", "label": "Wait", "duration_s": step["wait"]}
    if "play" in step:
        return {"index": index, "kind": "play", "label": "Play audio", "asset": step["play"]}
    if "tone" in step:
        tone = step["tone"] or {}
        return {
            "index": index,
            "kind": "tone",
            "label": "Play tone",
            "frequency_hz": tone.get("freq", 1000),
            "duration_s": tone.get("duration", 0.5),
        }
    if "hold" in step:
        return {
            "index": index,
            "kind": "record",
            "label": "Record caller",
            "duration_s": step["hold"],
        }
    if step.get("hangup"):
        return {"index": index, "kind": "hangup", "label": "Hang up"}
    if "dial" in step:
        return {"index": index, "kind": "bridge", "label": "Bridge call", "target": step["dial"]}
    if "repeat_from" in step:
        return {
            "index": index,
            "kind": "repeat",
            "label": "Repeat sequence",
            "from_index": step["repeat_from"],
        }
    return {"index": index, "kind": "unknown", "label": "Unknown step", "value": step}


class ScenarioCatalog:
    def __init__(self, scenarios_dir: Path | None = None) -> None:
        self.scenarios_dir = scenarios_dir or SCENARIOS_DIR

    def _load(self, path: Path) -> dict[str, Any]:
        data = yaml.safe_load(path.read_text())
        return validate_scenario(data, source=str(path))

    def _load_all(self) -> list[dict[str, Any]]:
        scenarios = []
        names: set[str] = set()
        for path in sorted(self.scenarios_dir.glob("*.yaml")):
            raw = self._load(path)
            if raw["name"] in names:
                raise ValueError(f"Duplicate scenario name {raw['name']!r} in {path}")
            names.add(raw["name"])
            scenarios.append(raw)
        return scenarios

    def validate(self, scenario: str | None = None) -> dict[str, Any]:
        """Inspect every selected file and report all authoring errors together."""

        explicit = Path(scenario) if scenario else None
        paths = (
            [explicit] if explicit is not None and explicit.is_file()
            else sorted(self.scenarios_dir.glob("*.yaml"))
        )
        entries: list[dict[str, Any]] = []
        names: dict[str, str] = {}
        for path in paths:
            entry: dict[str, Any] = {"path": str(path), "valid": False}
            try:
                raw = yaml.safe_load(path.read_text())
                if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                    entry["name"] = raw["name"]
                if scenario and explicit not in paths and entry.get("name") != scenario:
                    continue
                validate_scenario(raw, source=str(path))
                name = raw["name"]
                if name in names:
                    raise ValueError(f"Duplicate scenario name {name!r}; also defined in {names[name]}")
                names[name] = str(path)
                entry["valid"] = True
            except (OSError, ValueError, yaml.YAMLError) as exc:
                entry["error"] = str(exc)
            entries.append(entry)
        if not entries:
            entries.append({"valid": False, "error": f"No scenario files found for {scenario or self.scenarios_dir}"})
        return {"valid": all(entry["valid"] for entry in entries), "scenarios": entries}

    def names(self) -> list[str]:
        amd = [raw["name"] for raw in self._load_all()]
        return [*amd, *(entry["name"] for entry in scenario_entries())]

    def get_raw(self, name: str) -> dict[str, Any] | None:
        for data in self._load_all():
            if data["name"] == name:
                return data
        return None

    def get(self, name: str) -> dict[str, Any] | None:
        raw = self.get_raw(name)
        if raw:
            return self.describe(raw)
        return next(
            (
                entry
                for entry in scenario_entries()
                if entry["name"] == name or name in entry.get("aliases", [])
            ),
            None,
        )

    def list(self) -> list[dict[str, Any]]:
        amd = [self.describe(raw) for raw in self._load_all()]
        return [*amd, *scenario_entries()]

    def describe(self, raw: dict[str, Any]) -> dict[str, Any]:
        machine = raw.get("machine") or {}
        sequences = []
        for sequence_name, steps in machine.items():
            if not isinstance(steps, list):
                continue
            sequences.append(
                {
                    "name": sequence_name,
                    "steps": [_step(step, index) for index, step in enumerate(steps)],
                }
            )
        return {
            "name": raw["name"],
            "title": _title(raw["name"]),
            "kind": "amd",
            "description": "Answering-machine, voicemail, screener, or human-answer behavior.",
            "pstn_only": bool(raw.get("pstn_only")),
            "simulation_only": False,
            "sequences": sequences,
            "expectation_count": len(raw.get("expect") or {}),
            "expected_outcome": None,
            "has_dtmf": bool(machine.get("on_dtmf")) and machine.get("on_dtmf") != "ignore",
        }
