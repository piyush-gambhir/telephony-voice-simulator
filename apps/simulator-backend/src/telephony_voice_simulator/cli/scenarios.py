"""Scenario catalog, validation and the built-in IVR and PBX models."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..domains.catalog import ScenarioCatalog
from ..paths import SCENARIOS_DIR
from ..service import models
from .common import action, emit, group, pairs, service


def _list(args: argparse.Namespace) -> int:
    # The catalog alone: listing scenarios needs no database.
    scenarios = ScenarioCatalog().list()
    if args.kind:
        scenarios = [item for item in scenarios if item.get("kind") == args.kind]
    return emit(scenarios)


def _validate(args: argparse.Namespace) -> int:
    report = ScenarioCatalog(args.scenarios_dir).validate(args.scenario)
    emit(report)
    return 0 if report["valid"] else 1


def _simulate(args: argparse.Namespace) -> int:
    return emit(
        models.simulate_model(
            args.kind,
            args.scenario,
            pairs(args.set, "--set") if args.set else None,
        )
    )


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "scenarios", "AMD, IVR and PBX scenarios")
    listing = action(actions, "list", "list the catalog", _list)
    listing.add_argument("--kind", choices=("amd", "ivr", "pbx"))
    show = action(
        actions, "show", "one scenario in full", lambda a: emit(service().get_scenario(a.name))
    )
    show.add_argument("name")
    validate = action(actions, "validate", "validate AMD scenario files", _validate)
    selection = validate.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="every scenario file")
    selection.add_argument("--scenario", help="scenario name or path to a YAML file")
    validate.add_argument("--scenarios-dir", type=Path, default=SCENARIOS_DIR)
    simulate = action(
        actions,
        "simulate",
        "run built-in IVR journeys or PBX stories with no line or database",
        _simulate,
    )
    simulate.add_argument("scenario", nargs="?", help="scenario ID; omit to run them all")
    simulate.add_argument("--kind", choices=("ivr", "pbx"), default="ivr")
    simulate.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="EXTENSION=STATE",
        help="PBX extension state for this run: available|busy|offline|after_hours",
    )
