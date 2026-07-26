"""Built-in scenario packs beyond answering-machine detection."""

from .base import ScenarioDefinition, ScenarioKind, TimelineStep
from .ivr import definitions as ivr_definitions
from .pbx import definitions as pbx_definitions


def scenario_definitions() -> tuple[ScenarioDefinition, ...]:
    return (*ivr_definitions(), *pbx_definitions())


def scenario_entries() -> tuple[dict, ...]:
    return tuple(definition.as_catalog_entry() for definition in scenario_definitions())


__all__ = [
    "ScenarioDefinition",
    "ScenarioKind",
    "TimelineStep",
    "scenario_definitions",
    "scenario_entries",
]
