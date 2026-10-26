"""AMD benchmark: plan, run, compare, rescore and read reports."""

from __future__ import annotations

import argparse
import sys

from ..service import benchmarks
from .common import action, emit, group


def _selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--suite", help="benchmark suite YAML")
    parser.add_argument("--scenario", action="append", help="scenario name (repeatable)")
    parser.add_argument("--repeat", type=int, help="attempts per scenario")
    parser.add_argument(
        "--number",
        action="append",
        help="simulator line (repeatable; one concurrent lane per line)",
    )
    parser.add_argument("--concurrency", type=int, help="lanes to use, capped at the line count")
    parser.add_argument("--pace", type=float, help="seconds a lane waits between its own calls")


def _plan(args: argparse.Namespace) -> int:
    return emit(
        benchmarks.plan(
            suite=args.suite,
            scenarios=args.scenario,
            repeat=args.repeat,
            numbers=args.number,
            concurrency=args.concurrency,
            pace=args.pace,
        )
    )


def _run(args: argparse.Namespace) -> int:
    report = benchmarks.run(
        suite=args.suite,
        scenarios=args.scenario,
        repeat=args.repeat,
        numbers=args.number,
        concurrency=args.concurrency,
        pace=args.pace,
        audio=not args.no_audio,
        out=args.out,
        log=lambda line: print(line, file=sys.stderr, flush=True),
    )
    emit(report)
    return 0 if report["ok"] else 1


def _compare(args: argparse.Namespace) -> int:
    result = benchmarks.compare(reports=args.reports, out=args.out)
    if args.markdown:
        print(result["markdown"], end="")
        return 0
    return emit(result)


def _rescore(args: argparse.Namespace) -> int:
    result = benchmarks.rescore(report=args.report)
    if args.markdown:
        print(result["markdown"], end="")
        return 0
    return emit(result)


def _preflight(args: argparse.Namespace) -> int:
    result = benchmarks.preflight(report=args.report)
    emit(result)
    return 0 if result["healthy"] else 1


def _report(args: argparse.Namespace) -> int:
    result = benchmarks.get_report(args.report_id, markdown=args.markdown)
    if args.markdown:
        print(result["markdown"], end="")
        return 0
    return emit(result)


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "benchmark", "score a live agent against the scenario catalog")

    plan = action(actions, "plan", "show the resolved plan without placing calls", _plan)
    _selection(plan)

    run = action(
        actions,
        "run",
        "place real calls for every scenario and attempt; exits 1 unless all pass",
        _run,
    )
    _selection(run)
    run.add_argument("--no-audio", action="store_true", help="skip sim-side audio grading")
    run.add_argument("--out", help="report directory (default results/benchmark/<run_id>)")

    compare = action(actions, "compare", "side-by-side comparison of finished runs", _compare)
    compare.add_argument(
        "reports", nargs="+", metavar="[LABEL=]REPORT", help="report.json, directory or id"
    )
    compare.add_argument("--out", help="also write comparison.md into this directory")
    compare.add_argument("--markdown", action="store_true", help="print the markdown only")

    rescore = action(
        actions,
        "rescore",
        "re-read a finished run under the current scoring rules (places no calls)",
        _rescore,
    )
    rescore.add_argument("report", help="report.json, directory or id")
    rescore.add_argument("--markdown", action="store_true", help="print the markdown only")

    preflight = action(
        actions,
        "preflight",
        "exit 0 if that run's pipeline worked end to end, whatever the agent scored",
        _preflight,
    )
    preflight.add_argument("report", help="report.json, directory or id")

    action(
        actions,
        "reports",
        "list finished and partial runs",
        lambda _a: emit(benchmarks.list_reports()),
    )

    report = action(actions, "report", "one run's report", _report)
    report.add_argument("report_id")
    report.add_argument("--markdown", action="store_true", help="print the markdown only")
