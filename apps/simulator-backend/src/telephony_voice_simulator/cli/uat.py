"""UAT matrix: config-driven workflows against a hosted voice agent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..service import uat
from .common import action, emit, group


def _log(line: str) -> None:
    print(line, file=sys.stderr)


def _run(args: argparse.Namespace) -> int:
    report = uat.run(
        config=args.config, scenarios=args.scenario, output=args.output, log=_log
    )
    emit(report)
    return 0 if report["passed"] == report["total"] else 1


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "uat", "config-driven UAT matrix runner")
    action(actions, "configs", "UAT configs in the config directory",
           lambda _a: emit(uat.list_configs()))
    check = action(actions, "check", "validate a UAT config",
                   lambda a: emit(uat.check_config(config=a.config)))
    check.add_argument("--config", required=True)
    plan = action(actions, "plan", "dry run: render every action without executing",
                  lambda a: emit(uat.plan(config=a.config, scenarios=a.scenario)))
    plan.add_argument("--config", required=True)
    plan.add_argument("--scenario", action="append", default=[], help="only this scenario")
    run = action(actions, "run", "execute the matrix and write a JSON report", _run)
    run.add_argument("--config", required=True)
    run.add_argument("--scenario", action="append", default=[], help="only this scenario")
    run.add_argument("--output", type=Path, help="JSON report path")
