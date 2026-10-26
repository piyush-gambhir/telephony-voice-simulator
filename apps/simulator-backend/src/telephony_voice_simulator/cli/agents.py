"""Agents: drive an external voice agent through its REST API or a LiveKit room."""

from __future__ import annotations

import argparse
import sys

from ..service import agents
from .common import action, emit, group, run


def _progress(line: str) -> None:
    print(line, file=sys.stderr)


def _scenario_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenario", default=agents.DEFAULT_SCENARIO)
    parser.add_argument("--number", default="", help="line to dial; defaults to SIM_NUMBER")


def _trigger(args: argparse.Namespace) -> int:
    return emit(agents.trigger_call(scenario=args.scenario, number=args.number))


def _call(args: argparse.Namespace) -> int:
    result = agents.call_and_wait(
        scenario=args.scenario, number=args.number, timeout=args.timeout, log=_progress
    )
    emit(result)
    return 0 if result["completed"] else 1


def _record(args: argparse.Namespace) -> int:
    return emit(agents.call_record(call_id=args.call_id))


def _room(args: argparse.Namespace) -> int:
    summary = run(
        agents.run_rooms(
            scenario=args.scenario, all_scenarios=args.all, filter=args.filter, log=_progress
        )
    )
    emit(summary)
    return 0 if summary["passed"] == summary["total"] else 1


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "agents", "drive an external voice agent")
    trigger = action(
        actions, "trigger", "ask the agent's REST API to dial the simulator line", _trigger
    )
    _scenario_options(trigger)
    call = action(actions, "call", "trigger a call and wait for the agent's outcome", _call)
    _scenario_options(call)
    call.add_argument("--timeout", type=float, default=300.0, help="seconds to wait")
    record = action(actions, "record", "read the agent's record of a call", _record)
    record.add_argument("--call-id", required=True)
    room = action(actions, "room", "run scenarios against a LiveKit room", _room)
    room.add_argument("--scenario", help="catalog scenario name or path to a yaml file")
    room.add_argument("--all", action="store_true", help="run every scenario")
    room.add_argument("--filter", default="", help="substring filter with --all")
