"""Shared domain model for simulator scenario packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ScenarioKind = Literal["amd", "ivr", "pbx"]


@dataclass(frozen=True)
class TimelineStep:
    at: str
    actor: str
    kind: str
    label: str
    detail: str
    outcome: str = "in_progress"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "at": self.at,
            "actor": self.actor,
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
            "outcome": self.outcome,
            **self.metadata,
        }


@dataclass(frozen=True)
class ScenarioDefinition:
    name: str
    title: str
    kind: ScenarioKind
    description: str
    steps: tuple[TimelineStep, ...]
    expected_outcome: str
    has_dtmf: bool = False
    pstn_only: bool = False
    aliases: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_catalog_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "kind": self.kind,
            "description": self.description,
            "pstn_only": self.pstn_only,
            "simulation_only": True,
            "sequences": [
                {
                    "name": "main",
                    "steps": [step.as_dict(index) for index, step in enumerate(self.steps)],
                }
            ],
            "expectation_count": 1,
            "expected_outcome": self.expected_outcome,
            "has_dtmf": self.has_dtmf,
            "aliases": list(self.aliases),
            "metadata": self.metadata,
        }
