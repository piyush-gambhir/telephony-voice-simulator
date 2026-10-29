"""Regression suite: real calls against a deployed agent, pass/fail per case."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..evaluation import regression
from ..paths import RESULTS_DIR
from .common import action, emit, group, service


def _log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _run(args: argparse.Namespace) -> int:
    report = regression.run(
        suite_path=args.suite, cases=args.case, store=service().store, baseline=args.baseline,
        tolerance=args.tolerance, out_dir=args.out_dir, log=_log,
    )
    if args.markdown:
        print(regression.summary_table(report))
        print(f"\n{report['passed']} passed, {report['failed']} failed, "
              f"{report['indeterminate']} indeterminate. Report: {report['path']}")
    else:
        emit(report)
    return 0 if report["ok"] else 1


def _plan(args: argparse.Namespace) -> int:
    suite = regression.load_suite(args.suite)
    return emit([{k: c.get(k) for k in ("name", "kind", "scenario", "persona", "target", "expect")}
                 for c in regression.select(suite, args.case)])


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "regression", "regression suite against a deployed agent")
    plan = action(actions, "plan", "validate a suite and list the cases a run would place",
                  _plan)
    plan.add_argument("--suite", required=True, type=Path)
    plan.add_argument("--case", action="append", default=[], help="only this case")
    run = action(actions, "run", "place every case's call and report pass/fail (exit 1 on "
                 "any failure)", _run)
    run.add_argument("--suite", required=True, type=Path)
    run.add_argument("--case", action="append", default=[], help="only this case")
    run.add_argument("--baseline", type=Path, help="earlier report to compare against")
    run.add_argument("--tolerance", type=float, default=0.3,
                     help="allowed p95 latency growth over the baseline (default 0.3 = 30%%)")
    run.add_argument("--out-dir", type=Path, default=RESULTS_DIR / "regression")
    run.add_argument("--markdown", action="store_true", help="print a summary table")
