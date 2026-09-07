"""AMD benchmark suite: score a live agent against the scenario catalog.

A single graded call answers "did the agent handle this callee correctly?".
A benchmark answers the questions you actually ship on: how often does the
agent call a human a machine, how long does it take to decide, and which
callee shapes does it fail repeatedly rather than once.

The suite drives real PSTN calls. For every (scenario, attempt) pair it

  1. queues the scenario on the simulator line (its control API),
  2. asks the agent's own backend to place the outbound call
     (``agent_api``: real prompt, real model, real AMD configuration),
  3. waits for the agent's call record to reach a terminal status,
  4. grades the agent-reported end reason and detection layer against the
     scenario's ``expect`` block — the checks a sim-only run has to skip, and
  5. collects the simulator's own grading of the leg it answered.

The simulator grades every call against the compiled scenario it actually
played, so step 5 reads that analysis rather than re-deriving one: it already
holds the true timeline, the real DTMF presses, and the downloaded mailbox
recording.

It then aggregates a machine-vs-human confusion matrix, per-scenario pass
rates over repeats, and detection-latency percentiles.

Suite file (YAML):

    version: 1
    name: amd-core
    repeat: 3
    pace_seconds: 20
    call_timeout_seconds: 300
    numbers: ["+15550100000", "+15550100001"]
    scenarios: [stock_voicemail_beep_1000, business_receptionist_human]
    # or: scenarios: all
    # exclude: [number_disconnected_sit]

Each line is one concurrent lane. The scenario queue is per-number, so a lane
owns its line for the whole call and two lanes never race for the same queue;
n lines run n calls at once.

Usage:
    python -m telephony_voice_simulator.benchmark --suite config/benchmark.example.yaml
    python -m telephony_voice_simulator.benchmark --scenario stock_voicemail_beep_1000 --repeat 5
    python -m telephony_voice_simulator.benchmark --suite ... --dry-run
    python -m telephony_voice_simulator.benchmark --compare uat=a/report.json prod=b/report.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import yaml

from . import agent_api
from .paths import RESULTS_DIR, SCENARIOS_DIR
from .scenario_validation import validate_scenario

BENCHMARK_RESULTS_DIR = RESULTS_DIR / "benchmark"

# End reasons that mean "the agent concluded it was talking to a machine".
MACHINE_ENDED_PREFIXES = ("call.ending.voicemail", "call.ending.machine")
UNCONNECTED_STATUSES = {"failed", "no-answer", "no_answer", "busy", "canceled", "cancelled"}


# --------------------------------------------------------------------------
# scenario catalog
# --------------------------------------------------------------------------


def load_scenarios() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        raw = validate_scenario(yaml.safe_load(path.read_text()), source=str(path))
        name = raw.get("name")
        if name:
            raw["_file"] = path.name
            catalog[name] = raw
    return catalog


def expected_class(scenario: dict[str, Any]) -> str:
    """What is on the line: machine, human, screener, or unknown.

    Derived from the scenario's own expectations so the catalog stays the
    single source of truth. An explicit ``expect.callee_class`` wins when a
    scenario is too unusual to classify from its assertions.

    ``screener`` is its own class on purpose. A gate or call-screening
    assistant IS a machine that the agent must detect and navigate, but the
    call must still end as a live conversation — scoring it as either plain
    machine or plain human would hide the failure that matters.
    """
    expect = scenario.get("expect") or {}
    explicit = str(expect.get("callee_class") or "").strip().lower()
    if explicit in ("machine", "human", "screener"):
        return explicit
    if expect.get("detection_layer_absent"):
        return "human"

    required = [str(expect["ended_reason"])] if "ended_reason" in expect else []
    required += [str(r) for r in expect.get("ended_reason_any_of") or []]
    if required and all(r.startswith(MACHINE_ENDED_PREFIXES) for r in required):
        return "machine"

    banned_machine = any(
        str(r).startswith(MACHINE_ENDED_PREFIXES) for r in expect.get("ended_reason_not") or []
    )
    if banned_machine:
        # Forbidding every machine ending means the call has to end in
        # conversation. Whether something machine-like answered FIRST is what
        # separates a screener from a plain human pickup.
        return "screener" if expect.get("detection_layer_prefix_any_of") else "human"
    return "unknown"


def observed_class(outcome: dict[str, Any]) -> str:
    """What the agent DID, in the same three classes the scenarios use.

    Detection alone is not the verdict. On a screener the detector is SUPPOSED
    to fire — that is the machine gate — and the agent is then supposed to
    navigate it and stay on the line. Reading ``is_machine`` as "the agent
    treated this as a mailbox" scores every correctly-navigated screener as a
    machine, which is exactly backwards.

    So the ending decides machine vs live, and detection only distinguishes a
    screener from a plain human pickup among the live ones.
    """
    if str(outcome.get("status") or "").strip().lower() in UNCONNECTED_STATUSES:
        return "unknown"
    reason = outcome.get("ended_reason") or ""
    if reason.startswith(MACHINE_ENDED_PREFIXES):
        return "machine"
    if not reason:
        return "unknown"
    detected = outcome.get("is_machine") is True or bool(outcome.get("detection_layer"))
    return "screener" if detected else "human"


# --------------------------------------------------------------------------
# agent-record (webhook-side) checks
# --------------------------------------------------------------------------


def record_checks(expect: dict[str, Any], outcome: dict[str, Any]) -> list[dict[str, Any]]:
    """The ended_reason / detection_layer checks the sim side cannot see."""
    checks: list[dict[str, Any]] = []
    reason = outcome.get("ended_reason") or ""
    layer = outcome.get("detection_layer") or ""

    def add(name: str, passed: bool | None, detail: str) -> None:
        checks.append({"check": name, "passed": passed, "detail": detail})

    if expect.get("webhook_received"):
        add(
            "agent_record_received",
            bool(outcome.get("status")),
            f"status={outcome.get('status') or 'MISSING'}",
        )
    if "ended_reason" in expect:
        want = str(expect["ended_reason"])
        add("ended_reason", reason == want, f"want={want} got={reason or '(none)'}")
    if "ended_reason_any_of" in expect:
        want_any = [str(w) for w in expect["ended_reason_any_of"]]
        add(
            "ended_reason_any_of",
            reason in want_any,
            f"want in {want_any} got={reason or '(none)'}",
        )
    if "ended_reason_not" in expect:
        banned = [str(w) for w in expect["ended_reason_not"]]
        add(
            "ended_reason_not",
            bool(reason) and reason not in banned,
            f"banned={banned} got={reason or '(none)'}",
        )
    if "detection_layer_prefix_any_of" in expect:
        prefixes = [str(p) for p in expect["detection_layer_prefix_any_of"]]
        add(
            "detection_layer",
            any(layer.startswith(p) for p in prefixes),
            f"want prefix in {prefixes} got={layer or '(none)'}",
        )
    if expect.get("detection_layer_absent"):
        add("detection_layer_absent", not layer, f"got={layer or '(none)'}")
    return checks


# --------------------------------------------------------------------------
# one benchmark call
# --------------------------------------------------------------------------


def simulator_api() -> str:
    return os.environ.get("SIMULATOR_API_URL", "http://127.0.0.1:8978").rstrip("/")


def number_index() -> dict[str, str]:
    """phone number -> the simulator's managed-number id."""
    resp = httpx.get(f"{simulator_api()}/api/amd-numbers", timeout=30)
    resp.raise_for_status()
    return {row["phone_number"]: row["id"] for row in resp.json()}


def queue_scenario(scenario: str, number: str, index: dict[str, str]) -> None:
    """Queue exactly this scenario on the line's managed endpoint."""
    number_id = index.get(number)
    if not number_id:
        raise RuntimeError(
            f"{number} is not a managed simulator number. "
            f"POST {simulator_api()}/api/amd-numbers/sync after attaching it."
        )
    resp = httpx.post(
        f"{simulator_api()}/api/amd-numbers/{number_id}/queue",
        json={"scenario": scenario},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"queue failed {resp.status_code}: {resp.text[:300]}")


def find_sim_call(
    number: str,
    since: float,
    *,
    timeout: float = 240.0,
    exclude_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """The simulator's own record of the leg the agent placed at this line.

    The simulator grades every call it answers against the compiled scenario
    it actually played, so its ``analysis`` is better evidence than anything
    reconstructed here: it has the real timeline and the real DTMF presses.
    We wait for that analysis rather than re-deriving it.

    ``exclude_ids`` carries the legs this lane already consumed, so the
    timestamp tolerance can never re-match the lane's previous call.
    """
    seen = exclude_ids or set()
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{simulator_api()}/api/calls", timeout=30)
            resp.raise_for_status()
            calls = resp.json()
        except httpx.HTTPError:
            time.sleep(5)
            continue
        for call in calls:
            if call.get("to_address") != number or call.get("id") in seen:
                continue
            started = _parse_iso(call.get("started_at"))
            # Allow timestamp rounding only. A prior call on this line is not
            # evidence for this attempt, even if its grading arrived late.
            if started is None or started < since - 1:
                continue
            latest = call
            if (call.get("analysis") or {}).get("checks") is not None:
                return call
        time.sleep(8)
    # Timed out waiting for grading; the partial record still names the call.
    return latest


def _parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def run_once(
    config: agent_api.AgentApiConfig,
    *,
    scenario_name: str,
    scenario: dict[str, Any],
    number: str,
    attempt: int,
    call_timeout: float,
    grade_audio: bool,
    numbers: dict[str, str],
    graded_sids: set[str] | None = None,
    label: str = "",
) -> dict[str, Any]:
    started = time.time()
    row: dict[str, Any] = {
        "scenario": scenario_name,
        "attempt": attempt,
        "sim_number": number,
        "expected_class": expected_class(scenario),
        "started_at": started,
        "error": None,
        "agent_terminal": False,
    }

    try:
        queue_scenario(scenario_name, number, numbers)
        call_id, _ = agent_api.trigger_call(config, scenario=scenario_name, number=number)
    except Exception as exc:  # trigger/queue failures are results, not crashes
        row["error"] = f"{type(exc).__name__}: {exc}"
        # A type and a message alone cannot say WHERE a run died, and a failed
        # suite is expensive to reproduce, so keep the frames too.
        row["traceback"] = traceback.format_exc()[-2000:]
        row["observed_class"] = "unknown"
        row["passed"] = False
        return row

    row["agent_call_id"] = call_id
    print(f"{label} dialing   call_id={call_id}", flush=True)
    record = None
    try:
        record = agent_api.wait_for_terminal(
            config, call_id, timeout=call_timeout, interval=10.0, label=label
        )
    except (httpx.HTTPError, ValueError) as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["agent_terminal"] = agent_api.is_terminal(config, record)
    outcome = agent_api.extract_outcome(config, record)
    row.update(
        {
            "status": outcome["status"],
            "ended_reason": outcome["ended_reason"],
            "detection_layer": outcome["detection_layer"],
            "detection_delay": outcome["detection_delay"],
            "amd": outcome["amd"],
            "observed_class": observed_class(outcome),
        }
    )
    if not row["agent_terminal"]:
        row["error"] = row["error"] or "agent call record never reached a terminal status"

    expect = scenario.get("expect") or {}
    checks = [
        {
            "check": "agent_call_terminal",
            "passed": row["agent_terminal"],
            "detail": f"status={outcome['status'] or 'MISSING'}",
        },
        {
            "check": "agent_call_connected",
            "passed": row["agent_terminal"]
            and outcome["status"].strip().lower() not in UNCONNECTED_STATUSES,
            "detail": f"status={outcome['status'] or 'MISSING'}",
        },
        *record_checks(expect, outcome),
    ]

    if grade_audio:
        sim_call = find_sim_call(number, started, exclude_ids=graded_sids)
        sid = (sim_call or {}).get("id")
        row["sim_call_sid"] = sid
        if sid is not None and graded_sids is not None:
            graded_sids.add(sid)
        analysis = (sim_call or {}).get("analysis") or {}
        if analysis.get("checks") is not None:
            row["sim_status"] = sim_call.get("status")
            row["sim_scenario"] = sim_call.get("scenario")
            checks.append(
                {
                    "check": "sim_scenario_matches",
                    "passed": row["sim_scenario"] == scenario_name,
                    "detail": f"want={scenario_name} got={row['sim_scenario']}",
                }
            )
            row["sim_recordings"] = [r.get("local_path") for r in sim_call.get("recordings") or []]
            checks.extend(analysis["checks"])
        elif sim_call is not None:
            checks.append(
                {
                    "check": "sim_leg_graded",
                    "passed": False,
                    "detail": (
                        f"leg {sid} answered (status={sim_call.get('status')}) but the "
                        "simulator never published its analysis"
                    ),
                }
            )
        else:
            checks.append(
                {
                    "check": "sim_leg_present",
                    "passed": False,
                    "detail": "the agent never reached the sim line",
                }
            )

    row["checks"] = checks
    if row["error"] or any(c["passed"] is False for c in checks):
        row["passed"] = False
    elif not checks or any(c["passed"] is None for c in checks):
        row["passed"] = None
    else:
        row["passed"] = True
    row["duration_s"] = round(time.time() - started, 1)
    return row


# --------------------------------------------------------------------------
# aggregation and reporting
# --------------------------------------------------------------------------


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matrix: dict[str, dict[str, int]] = {}
    for row in rows:
        want = row.get("expected_class", "unknown")
        got = row.get("observed_class", "unknown")
        matrix.setdefault(want, {}).setdefault(got, 0)
        matrix[want][got] += 1

    per_scenario: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = per_scenario.setdefault(
            row["scenario"],
            {
                "attempts": 0,
                "passed": 0,
                "failed": 0,
                "indeterminate": 0,
                "delays": [],
                "failures": [],
            },
        )
        entry["attempts"] += 1
        if row.get("passed") is True:
            entry["passed"] += 1
        elif row.get("passed") is False:
            entry["failed"] += 1
            entry["failures"].append(
                {
                    "attempt": row["attempt"],
                    "error": row.get("error"),
                    "failed_checks": [
                        f"{c['check']}: {c['detail']}"
                        for c in row.get("checks", [])
                        if c["passed"] is False
                    ],
                }
            )
        else:
            entry["indeterminate"] += 1
        if _valid_delay(row.get("detection_delay")):
            entry["delays"].append(float(row["detection_delay"]))

    for entry in per_scenario.values():
        delays = entry.pop("delays")
        decided_count = entry["passed"] + entry["failed"]
        entry["pass_rate"] = round(entry["passed"] / decided_count, 3) if decided_count else None
        entry["detection_delay"] = _delay_stats(delays)

    all_delays = [
        float(r["detection_delay"]) for r in rows if _valid_delay(r.get("detection_delay"))
    ]
    decided = [r for r in rows if r.get("passed") is not None]
    machine_rows = [r for r in rows if r.get("expected_class") == "machine"]
    # A screener misread as a mailbox is the same production failure as a
    # human misread as one: the agent talks to a recording and hangs up on a
    # reachable person, so both belong in the false-machine rate.
    live_rows = [r for r in rows if r.get("expected_class") in ("human", "screener")]
    return {
        "calls": len(rows),
        "passed": sum(1 for r in rows if r.get("passed") is True),
        "failed": sum(1 for r in rows if r.get("passed") is False),
        "indeterminate": sum(1 for r in rows if r.get("passed") is None),
        "pass_rate": round(sum(1 for r in decided if r["passed"]) / len(decided), 3)
        if decided
        else None,
        "confusion_matrix": matrix,
        # The two errors that cost money in production: leaving a voicemail
        # into a live conversation, and conversing with a mailbox.
        "false_human_rate": _rate(machine_rows, {"human", "screener"}),
        "false_machine_rate": _rate(live_rows, {"machine"}),
        "classification_coverage": round(
            sum(r.get("observed_class", "unknown") != "unknown" for r in rows) / len(rows), 3
        )
        if rows
        else None,
        "detection_delay": _delay_stats(all_delays),
        "per_scenario": per_scenario,
    }


def _valid_delay(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _rate(rows: list[dict[str, Any]], wrong_classes: set[str]) -> float | None:
    classified = [r for r in rows if r.get("observed_class", "unknown") != "unknown"]
    if not classified:
        return None
    return round(
        sum(r.get("observed_class") in wrong_classes for r in classified) / len(classified), 3
    )


def _delay_stats(delays: list[float]) -> dict[str, float] | None:
    if not delays:
        return None
    ordered = sorted(delays)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 3),
        "median": round(statistics.median(ordered), 3),
        "p90": round(ordered[math.ceil(len(ordered) * 0.9) - 1], 3),
        "max": round(ordered[-1], 3),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# AMD benchmark — {report['suite']}",
        "",
        f"- run: `{report['run_id']}`",
        f"- completion: {summary['calls']}/{report.get('planned_calls', summary['calls'])} planned calls",
        f"- lines: {', '.join(f'`{n}`' for n in report['sim_numbers'])}",
        f"- calls: {summary['calls']} ({summary['passed']} passed, "
        f"{summary['failed']} failed, {summary['indeterminate']} indeterminate)",
        f"- pass rate: {_pct(summary['pass_rate'])}",
        f"- classified calls: {_pct(summary.get('classification_coverage'))}",
        f"- machine scored as human: {_pct(summary['false_human_rate'])}",
        f"- live callee scored as machine: {_pct(summary['false_machine_rate'])}",
    ]
    delay = summary["detection_delay"]
    if delay:
        lines.append(
            f"- detection delay: median {delay['median']}s, p90 {delay['p90']}s, max {delay['max']}s"
        )

    # Every class is both an expected and an observed outcome, so the matrix
    # must carry a screener COLUMN too — dropping it silently reassigns
    # correctly-navigated screeners to whatever columns remain.
    classes = ("machine", "screener", "human", "unknown")
    lines += [
        "",
        "## Confusion matrix",
        "",
        "| expected \\ observed | " + " | ".join(classes) + " |",
        "|---" * (len(classes) + 1) + "|",
    ]
    for want in classes:
        row = report["summary"]["confusion_matrix"].get(want, {})
        if not row:
            continue
        cells = " | ".join(str(row.get(got, 0)) for got in classes)
        lines.append(f"| {want} | {cells} |")

    lines += [
        "",
        "## Per scenario",
        "",
        "| scenario | expected | pass | attempts | rate | median delay |",
        "|---|---|---|---|---|---|",
    ]
    for name, entry in sorted(report["summary"]["per_scenario"].items()):
        want = next((r["expected_class"] for r in report["rows"] if r["scenario"] == name), "?")
        delay = entry["detection_delay"]
        lines.append(
            f"| {name} | {want} | {entry['passed']} | {entry['attempts']} | "
            f"{_pct(entry['pass_rate'])} | {delay['median'] if delay else '-'} |"
        )

    failing = {n: e for n, e in report["summary"]["per_scenario"].items() if e["failed"]}
    if failing:
        lines += ["", "## Failures", ""]
        for name, entry in sorted(failing.items()):
            lines.append(f"### {name} ({entry['failed']}/{entry['attempts']})")
            for failure in entry["failures"]:
                if failure["error"]:
                    lines.append(f"- attempt {failure['attempt']}: {failure['error']}")
                for check in failure["failed_checks"]:
                    lines.append(f"- attempt {failure['attempt']}: {check}")
            lines.append("")
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def resolve_plan(args, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    suite: dict[str, Any] = {}
    if args.suite:
        loaded = yaml.safe_load(Path(args.suite).expanduser().read_text())
        suite = {} if loaded is None else loaded
    if not isinstance(suite, dict):
        sys.exit("benchmark suite must be a YAML mapping")
    if type(suite.get("version", 1)) is not int or suite.get("version", 1) != 1:
        sys.exit("unsupported benchmark suite version; expected 1")

    selected = args.scenario or suite.get("scenarios") or "all"
    if isinstance(selected, str):
        selected = list(catalog) if selected == "all" else [selected]
    if not isinstance(selected, list) or any(not isinstance(n, str) for n in selected):
        sys.exit("scenarios must be a list of names or 'all'")
    exclusions = suite.get("exclude") or []
    if not isinstance(exclusions, list) or any(not isinstance(n, str) for n in exclusions):
        sys.exit("exclude must be a list of scenario names")
    excluded = set(exclusions)
    names = [n for n in selected if n not in excluded]
    if len(names) != len(set(names)):
        sys.exit("scenario names must be unique; use repeat for multiple attempts")

    unknown = [n for n in names if n not in catalog]
    if unknown:
        sys.exit(f"unknown scenario(s): {', '.join(unknown)}")
    if not names:
        sys.exit("no scenarios selected")

    numbers = list(args.number or []) or _suite_numbers(suite)
    if any(not isinstance(n, str) or not n.strip() for n in numbers):
        sys.exit("simulator numbers must be nonempty strings")
    numbers = [n.strip() for n in numbers]
    if len(numbers) != len(set(numbers)):
        sys.exit("simulator numbers must be unique; duplicate lanes would race")
    default_name = Path(args.suite).stem if args.suite else "ad-hoc"
    repeat = args.repeat if args.repeat is not None else suite.get("repeat", 1)
    # One lane per line: the scenario queue is per-number, so two concurrent
    # calls to the same line would race for it and mislabel each other.
    concurrency = (
        args.concurrency
        if args.concurrency is not None
        else suite.get("concurrency", len(numbers) or 1)
    )
    for field, value in (("repeat", repeat), ("concurrency", concurrency)):
        if type(value) is not int or value < 1:
            sys.exit(f"{field} must be a positive integer")
    pace = args.pace if args.pace is not None else suite.get("pace_seconds", 15.0)
    timeout = suite.get("call_timeout_seconds", 300.0)
    for field, value, minimum in (("pace_seconds", pace, 0), ("call_timeout_seconds", timeout, 0)):
        if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
            sys.exit(f"{field} must be a finite nonnegative number")
    if timeout == 0:
        sys.exit("call_timeout_seconds must be positive")
    return {
        "name": suite.get("name") or default_name,
        "scenarios": names,
        "repeat": repeat,
        "pace_seconds": float(pace),
        "call_timeout_seconds": float(timeout),
        "sim_numbers": numbers,
        "concurrency": max(1, min(concurrency, len(numbers) or 1)),
    }


def _suite_numbers(suite: dict[str, Any]) -> list[str]:
    numbers = suite.get("numbers") or suite.get("sim_numbers")
    if numbers is not None:
        if not isinstance(numbers, list):
            sys.exit("numbers must be a list of quoted phone numbers")
        return numbers
    single = suite.get("sim_number")
    return [single] if single else []


def build_jobs(plan: dict[str, Any]) -> list[tuple[str, int]]:
    """(scenario, attempt) pairs, attempt-major so a partial run stays balanced.

    Interleaving this way means an interrupted or aborted suite has covered
    every scenario once before any scenario has been covered twice.
    """
    return [
        (name, attempt) for attempt in range(1, plan["repeat"] + 1) for name in plan["scenarios"]
    ]


def render_comparison(reports: list[tuple[str, dict[str, Any]]]) -> str:
    """Side-by-side markdown for the same suite run against two-plus agents."""
    labels = [label for label, _ in reports]
    header = "| metric | " + " | ".join(labels) + " |"
    divider = "|---" * (len(labels) + 1) + "|"
    lines = ["# AMD benchmark comparison", "", header, divider]

    def row(name: str, values: list[str]) -> None:
        lines.append(f"| {name} | " + " | ".join(values) + " |")

    summaries = [report["summary"] for _, report in reports]
    row("calls", [str(s["calls"]) for s in summaries])
    row("pass rate", [_pct(s["pass_rate"]) for s in summaries])
    row("machine scored as human", [_pct(s["false_human_rate"]) for s in summaries])
    row("live callee scored as machine", [_pct(s["false_machine_rate"]) for s in summaries])
    row(
        "detection delay (median)",
        [str(s["detection_delay"]["median"]) if s["detection_delay"] else "-" for s in summaries],
    )
    row(
        "detection delay (p90)",
        [str(s["detection_delay"]["p90"]) if s["detection_delay"] else "-" for s in summaries],
    )

    scenarios = sorted({name for s in summaries for name in s["per_scenario"]})
    lines += ["", "## Per scenario pass rate", "", header.replace("metric", "scenario"), divider]
    for name in scenarios:
        cells = []
        for summary in summaries:
            entry = summary["per_scenario"].get(name)
            cells.append(
                f"{_pct(entry['pass_rate'])} ({entry['passed']}/{entry['attempts']})"
                if entry
                else "-"
            )
        row(name, cells)

    # A scenario that passes in one environment and fails in another is the
    # only thing a comparison can tell you that two separate reports cannot.
    diverged = []
    for name in scenarios:
        rates = [s["per_scenario"].get(name, {}).get("pass_rate") for s in summaries]
        known = [r for r in rates if r is not None]
        if len(known) > 1 and max(known) - min(known) >= 0.5:
            diverged.append((name, rates))
    if diverged:
        lines += ["", "## Diverged", ""]
        for name, rates in diverged:
            cells = ", ".join(f"{label}={_pct(r)}" for label, r in zip(labels, rates))
            lines.append(f"- **{name}**: {cells}")
    return "\n".join(lines) + "\n"


def rescore_report(report: dict[str, Any]) -> dict[str, Any]:
    """Recompute observed classes and the summary from the stored rows.

    Every row keeps the raw evidence (``ended_reason``, ``amd``,
    ``detection_layer``), so a scoring change never means re-dialling: an
    already-paid-for run can be re-read under the corrected rules.
    """
    rescored = dict(report)
    rows = []
    for row in report.get("rows") or []:
        row = dict(row)
        amd = row.get("amd") if isinstance(row.get("amd"), dict) else {}
        row["observed_class"] = observed_class(
            {
                "status": row.get("status") or "",
                "ended_reason": row.get("ended_reason") or "",
                "is_machine": (amd or {}).get("is_machine"),
                "detection_layer": row.get("detection_layer") or "",
            }
        )
        rows.append(row)
    rescored["rows"] = rows
    rescored["summary"] = summarize(rows)
    return rescored


def preflight_health(report: dict[str, Any]) -> tuple[bool, str]:
    """Did the pipeline work end to end, regardless of how the agent scored?

    A preflight exists to catch plumbing: a dead token, an unroutable mapping
    id, a line the simulator does not manage, a tunnel that is down. It must
    NOT gate on the scenario's assertions — an agent that genuinely fails
    ``stock_voicemail_beep_1000`` is a result worth collecting 93 times over,
    not a reason to refuse to start.
    """
    rows = report.get("rows") or []
    if not rows:
        return False, "no call was attempted"
    row = rows[0]
    if row.get("error"):
        return False, str(row["error"]).splitlines()[0]
    if not row.get("agent_call_id"):
        return False, "the agent never returned a call id"
    if not row.get("ended_reason"):
        return False, (
            f"the agent call never reported an end reason (status={row.get('status') or 'none'})"
        )
    if not row.get("sim_call_sid"):
        return False, "the agent never reached the simulator line"
    graded = [c for c in row.get("checks") or [] if c.get("passed") is not None]
    if not graded:
        return False, "nothing was graded on either side"
    failed = [c["check"] for c in row.get("checks") or [] if c.get("passed") is False]
    verdict = "agent passed" if not failed else f"agent failed {', '.join(failed)}"
    return True, f"pipeline healthy ({len(graded)} checks graded; {verdict})"


def _write_report(out_dir: Path, report: dict[str, Any]) -> None:
    for name, content in (
        ("report.json", json.dumps(report, indent=2, default=str)),
        ("report.md", render_markdown(report)),
    ):
        temporary = out_dir / f".{name}.tmp"
        temporary.write_text(content)
        temporary.replace(out_dir / name)


def run_suite(
    config: agent_api.AgentApiConfig,
    plan: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    *,
    out_dir: Path,
    grade_audio: bool,
) -> dict[str, Any]:
    """Run every job across the line pool, one lane per line."""
    jobs = build_jobs(plan)
    total = len(jobs)
    run_id = f"bench-{uuid.uuid4().hex[:10]}"
    numbers = plan["sim_numbers"]
    lanes = plan["concurrency"]
    if not numbers or len(numbers) != len(set(numbers)):
        raise ValueError("a benchmark requires unique simulator numbers")
    if not jobs or lanes < 1 or lanes > len(numbers):
        raise ValueError("a benchmark requires jobs and one lane per selected number")
    if not config.record_url:
        raise ValueError("AGENT_API_RECORD_URL is required before placing benchmark calls")
    out_dir.mkdir(parents=True, exist_ok=True)
    number_ids = number_index()
    missing = [n for n in numbers[:lanes] if n not in number_ids]
    if missing:
        sys.exit(
            f"not managed by the simulator at {simulator_api()}: {', '.join(missing)}\n"
            f"attach them, then POST {simulator_api()}/api/amd-numbers/sync"
        )

    pending = deque(jobs)
    rows: list[dict[str, Any]] = []
    lock = threading.Lock()
    report: dict[str, Any] = {}

    def snapshot() -> dict[str, Any]:
        return {
            "run_id": run_id,
            "suite": plan["name"],
            "sim_numbers": numbers[:lanes],
            "plan": plan,
            "planned_calls": total,
            "complete": len(rows) == total,
            "rows": list(rows),
            "summary": summarize(rows),
        }

    def lane(number: str) -> None:
        nonlocal report
        graded: set[str] = set()
        first = True
        # Lanes interleave, so a line that only makes sense next to an earlier
        # header is unreadable. Every line carries lane + progress + scenario,
        # which also makes a single lane greppable: `| grep '^L3'`.
        lane_no = numbers.index(number) + 1
        width = len(str(total))
        while True:
            with lock:
                if not pending:
                    return
                name, attempt = pending.popleft()
                position = total - len(pending)
            if not first and plan["pace_seconds"]:
                time.sleep(plan["pace_seconds"])
            first = False
            label = f"L{lane_no} {number} {position:>{width}}/{total} {name[:30]:30} a{attempt} |"
            row = run_once(
                config,
                scenario_name=name,
                scenario=catalog[name],
                number=number,
                attempt=attempt,
                call_timeout=plan["call_timeout_seconds"],
                grade_audio=grade_audio,
                numbers=number_ids,
                graded_sids=graded,
                label=label,
            )
            verdict = {True: "PASS", False: "FAIL", None: "INDET"}[row["passed"]]
            failed = [c["check"] for c in row.get("checks") or [] if c["passed"] is False]
            detail = f" failed={','.join(failed)}" if failed else ""
            print(
                f"{label} {verdict} {row['expected_class']}->{row.get('observed_class')} "
                f"{row.get('ended_reason') or '-'}{detail}",
                flush=True,
            )
            # Write after every call so an interrupted overnight run still
            # leaves usable evidence.
            with lock:
                rows.append(row)
                report = snapshot()
                _write_report(out_dir, report)
            if row.get("agent_terminal") is False:
                # A timed-out call may still occupy the line. Reusing it could
                # consume another scenario's queue entry and corrupt scoring.
                print(f"{label} lane stopped: agent call is not terminal", flush=True)
                return

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        list(pool.map(lane, numbers[:lanes]))

    return report or snapshot()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    parser.add_argument("--no-audio", action="store_true", help="skip sim-side audio grading")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    parser.add_argument("--out", help="report directory (default results/benchmark/<run_id>)")
    parser.add_argument(
        "--compare",
        nargs="+",
        metavar="LABEL=REPORT.JSON",
        help="render a side-by-side comparison of finished runs and exit",
    )
    parser.add_argument(
        "--preflight-check",
        metavar="REPORT.JSON",
        help="exit 0 if that run's pipeline worked end to end, whatever the agent scored",
    )
    parser.add_argument(
        "--rescore",
        metavar="REPORT.JSON",
        help="re-read a finished run under the current scoring rules (no calls placed)",
    )
    args = parser.parse_args()

    if args.rescore:
        source = Path(args.rescore).expanduser()
        report = rescore_report(json.loads(source.read_text()))
        source.with_name("report.rescored.json").write_text(
            json.dumps(report, indent=2, default=str)
        )
        source.with_name("report.rescored.md").write_text(render_markdown(report))
        print(f"rescored -> {source.with_name('report.rescored.md')}")
        print(render_markdown(report))
        return 0

    if args.preflight_check:
        report = json.loads(Path(args.preflight_check).expanduser().read_text())
        healthy, detail = preflight_health(report)
        print(f"preflight: {'OK' if healthy else 'BROKEN'} — {detail}")
        return 0 if healthy else 1

    if args.compare:
        reports = []
        for item in args.compare:
            label, _, path = item.partition("=")
            if not path:
                label, path = Path(label).parent.name, label
            reports.append((label, json.loads(Path(path).expanduser().read_text())))
        text = render_comparison(reports)
        if args.out:
            target = Path(args.out).expanduser()
            target.mkdir(parents=True, exist_ok=True)
            (target / "comparison.md").write_text(text)
            print(f"comparison -> {target}/comparison.md")
        print(text)
        return 0

    catalog = load_scenarios()
    plan = resolve_plan(args, catalog)
    if not plan["sim_numbers"]:
        number = os.environ.get("SIM_NUMBER", "").strip()
        if number:
            plan["sim_numbers"] = [number]
        elif not args.dry_run:
            parser.error("provide --number, suite numbers, or SIM_NUMBER before placing calls")
        plan["concurrency"] = 1
    total = len(plan["scenarios"]) * plan["repeat"]

    print(
        f"suite={plan['name']} scenarios={len(plan['scenarios'])} "
        f"repeat={plan['repeat']} calls={total}"
    )
    print(
        f"lanes={plan['concurrency']} of {len(plan['sim_numbers'])} line(s) "
        f"pace={plan['pace_seconds']}s"
    )
    if args.dry_run:
        for number in plan["sim_numbers"][: plan["concurrency"]]:
            print(f"  lane {number}")
        for name in plan["scenarios"]:
            print(f"  {name}  (expects {expected_class(catalog[name])})")
        return 0

    config = agent_api.load_config()
    out_dir = (
        Path(args.out).expanduser()
        if args.out
        else BENCHMARK_RESULTS_DIR / f"bench-{uuid.uuid4().hex[:10]}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_suite(config, plan, catalog, out_dir=out_dir, grade_audio=not args.no_audio)
    print(f"\nreport -> {out_dir}/report.md")
    print(render_markdown(report))
    return (
        0
        if report["complete"]
        and report["summary"]["failed"] == 0
        and report["summary"]["indeterminate"] == 0
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
