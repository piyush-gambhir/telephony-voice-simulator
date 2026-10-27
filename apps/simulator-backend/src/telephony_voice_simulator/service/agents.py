"""Drive an external voice agent: through its REST API, or in a LiveKit room."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml

from ..integrations import agent_api
from ..integrations.livekit import runner
from ..paths import SCENARIOS_DIR
from .errors import ConflictError, NotFoundError, ValidationError

DEFAULT_SCENARIO = "stock_voicemail_beep_1000"


def _require_record_url(config: agent_api.AgentApiConfig) -> None:
    if not config.record_url:
        raise ValidationError("AGENT_API_RECORD_URL is required to read call outcomes")


def trigger_call(*, scenario: str = DEFAULT_SCENARIO, number: str = "") -> dict[str, Any]:
    """Ask the agent's backend to dial the simulator line; returns the call id and raw response."""
    config = agent_api.load_config()
    number = number or agent_api.sim_number()
    try:
        call_id, response = agent_api.trigger_call(config, scenario=scenario, number=number)
    except httpx.HTTPError as exc:
        raise ConflictError(f"Agent API request failed: {exc}") from exc
    except (ValueError, RuntimeError) as exc:
        raise ValidationError(str(exc)) from exc
    return {"call_id": call_id, "scenario": scenario, "number": number, "response": response}


def call_and_wait(
    *,
    scenario: str = DEFAULT_SCENARIO,
    number: str = "",
    timeout: float = 300.0,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Trigger a call and poll its record until terminal or timeout.

    ``completed`` is false on timeout, with an empty outcome.
    """
    config = agent_api.load_config()
    _require_record_url(config)
    number = number or agent_api.sim_number()
    try:
        call_id, _ = agent_api.trigger_call(config, scenario=scenario, number=number)
        if log:
            log(f"triggered call_id={call_id} scenario={scenario} -> dials {number}")
        record = agent_api.wait_for_terminal(config, call_id, timeout=timeout, log=log)
    except httpx.HTTPError as exc:
        raise ConflictError(f"Agent API request failed: {exc}") from exc
    except (ValueError, RuntimeError) as exc:
        raise ValidationError(str(exc)) from exc
    return {
        "call_id": call_id,
        "scenario": scenario,
        "number": number,
        "completed": record is not None,
        "outcome": agent_api.extract_outcome(config, record),
    }


def call_record(*, call_id: str) -> dict[str, Any]:
    """The agent's own record of a call plus its normalized outcome."""
    config = agent_api.load_config(require_trigger=False)
    _require_record_url(config)
    try:
        record = agent_api.fetch_record(config, call_id)
    except httpx.HTTPError as exc:
        raise ConflictError(f"Agent API request failed: {exc}") from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if record is None:
        raise NotFoundError(f"No agent call record for {call_id}")
    return {"record": record, "outcome": agent_api.extract_outcome(config, record)}


def _resolve_scenario(scenario: str) -> Path:
    """A path to a yaml file, or a catalog scenario by its name or file stem."""
    explicit = Path(scenario).expanduser()
    if explicit.is_file():
        return explicit
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        if path.stem == scenario:
            return path
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(raw, dict) and raw.get("name") == scenario:
            return path
    raise NotFoundError(f"Unknown scenario: {scenario}")


async def run_rooms(
    *,
    scenario: str | None = None,
    all_scenarios: bool = False,
    filter: str = "",
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Run one scenario (catalog name or yaml path) or all, in a LiveKit room.

    Returns total, passed, results_dir and the per-scenario reports.
    """
    if scenario:
        paths = [_resolve_scenario(scenario)]
    elif all_scenarios:
        paths = sorted(SCENARIOS_DIR.glob("*.yaml"))
        if filter:
            paths = [p for p in paths if filter in p.name]
    else:
        raise ValidationError("Pass a scenario or all_scenarios")
    if not paths:
        raise NotFoundError("No scenarios matched")
    return await runner.run_scenarios(paths, log=log)
