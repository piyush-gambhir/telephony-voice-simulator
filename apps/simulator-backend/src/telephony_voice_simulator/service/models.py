"""Run the built-in IVR journeys and PBX call stories without a line or a database."""

from __future__ import annotations

from typing import Any

from ..domains.ivr import SCENARIOS as IVR_SCENARIOS
from ..domains.ivr import get_scenario as get_ivr_scenario
from ..domains.ivr import simulate as simulate_ivr
from ..domains.pbx.stories import EXTENSIONS as PBX_EXTENSIONS
from ..domains.pbx.stories import SCENARIOS as PBX_SCENARIOS
from ..domains.pbx.stories import ExtensionState
from ..domains.pbx.stories import get_scenario as get_pbx_scenario
from ..domains.pbx.stories import simulate as simulate_pbx
from .errors import ValidationError

EXTENSION_STATES = ("available", "busy", "offline", "after_hours")


def pbx_extensions() -> list[dict[str, str]]:
    """Extensions a PBX story run may override, for pickers and validation messages."""
    return [{"id": extension.id, "name": extension.name} for extension in PBX_EXTENSIONS]


def _validated_overrides(overrides: dict[str, str]) -> dict[str, ExtensionState]:
    known = {extension.id for extension in PBX_EXTENSIONS}
    for extension_id, state in overrides.items():
        if extension_id not in known:
            raise ValidationError(f"Unknown PBX extension '{extension_id}'")
        if state not in EXTENSION_STATES:
            raise ValidationError(
                f"Extension state must be one of {', '.join(EXTENSION_STATES)}"
            )
    return dict(overrides)  # type: ignore[return-value]


def _pbx_result(scenario, overrides: dict[str, ExtensionState]) -> dict[str, Any]:
    steps, outcome = simulate_pbx(scenario, overrides)
    return {
        "scenario_id": scenario.name,
        "title": scenario.title,
        "caller": scenario.caller,
        "caller_number": scenario.caller_number,
        "dialed_number": scenario.dialed_number,
        "intent": scenario.intent,
        "outcome": outcome,
        "expected_summary": scenario.expected_summary,
        "overrides": overrides,
        "timeline": [step.as_dict(index) for index, step in enumerate(steps)],
    }


def _ivr_result(scenario) -> dict[str, Any]:
    steps, outcome = simulate_ivr(scenario)
    connected_to = next(
        (
            step.metadata.get("connected_to")
            for step in reversed(steps)
            if step.metadata.get("connected_to")
        ),
        None,
    )
    return {
        "scenario_id": scenario.name,
        "title": scenario.title,
        "caller_number": scenario.caller_number,
        "public_number": scenario.public_number,
        "outcome": outcome,
        "connected_to": connected_to,
        "attempts": sum(step.kind == "gather" for step in steps),
        "timeline": [step.as_dict(index) for index, step in enumerate(steps)],
    }


def simulate_model(
    kind: str,
    scenario_id: str | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Run one built-in model scenario, or all of them when no ID is given.

    ``overrides`` sets PBX extension states for this run only.
    """
    if kind == "pbx":
        states = _validated_overrides(overrides or {})
        if scenario_id is None:
            return [_pbx_result(scenario, states) for scenario in PBX_SCENARIOS]
        scenario = get_pbx_scenario(scenario_id)
        if scenario is None:
            known = ", ".join(item.name for item in PBX_SCENARIOS)
            raise ValidationError(f"Unknown PBX scenario '{scenario_id}'. Known: {known}")
        return _pbx_result(scenario, states)
    if kind != "ivr":
        raise ValidationError("Model kind must be ivr or pbx")
    if overrides:
        raise ValidationError("Extension overrides apply to PBX stories only")
    if scenario_id is None:
        return [_ivr_result(scenario) for scenario in IVR_SCENARIOS]
    scenario = get_ivr_scenario(scenario_id)
    if scenario is None:
        known = ", ".join(item.name for item in IVR_SCENARIOS)
        raise ValidationError(f"Unknown IVR scenario '{scenario_id}'. Known: {known}")
    return _ivr_result(scenario)
