"""Unified catalog for AMD YAML and built-in IVR/PBX scenario packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..domains import scenario_entries
from ..paths import SCENARIOS_DIR


def _title(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _step(step: dict[str, Any], index: int) -> dict[str, Any]:
    if "wait" in step:
        return {"index": index, "kind": "wait", "label": "Wait", "duration_s": step["wait"]}
    if "play" in step:
        return {"index": index, "kind": "play", "label": "Play audio", "asset": step["play"]}
    if "tone" in step:
        tone = step["tone"]
        return {
            "index": index,
            "kind": "tone",
            "label": "Play tone",
            "frequency_hz": tone.get("freq"),
            "duration_s": tone.get("duration"),
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
        if not isinstance(data, dict) or not data.get("name"):
            raise ValueError(f"Invalid scenario file: {path}")
        return data

    def names(self) -> list[str]:
        amd = [self._load(path)["name"] for path in sorted(self.scenarios_dir.glob("*.yaml"))]
        return [*amd, *(entry["name"] for entry in scenario_entries())]

    def get_raw(self, name: str) -> dict[str, Any] | None:
        for path in sorted(self.scenarios_dir.glob("*.yaml")):
            data = self._load(path)
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
        amd = [
            self.describe(self._load(path)) for path in sorted(self.scenarios_dir.glob("*.yaml"))
        ]
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
            "has_dtmf": "on_dtmf" in raw,
        }
