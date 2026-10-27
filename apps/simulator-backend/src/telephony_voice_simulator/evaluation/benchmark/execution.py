"""Live execution: queue a scenario, have the agent dial, grade both sides, run the lane pool."""

from __future__ import annotations

import os
import threading
import time
import traceback
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ...integrations import agent_api
from ...service.errors import ValidationError
from .plan import build_jobs
from .reports import write_report
from .scoring import (
    UNCONNECTED_STATUSES,
    expected_class,
    observed_class,
    record_checks,
    summarize,
)


def new_run_id() -> str:
    return f"bench-{uuid.uuid4().hex[:10]}"


def _silent(_line: str) -> None:
    pass


def simulator_api() -> str:
    return os.environ.get("SIMULATOR_API_URL", "http://127.0.0.1:8978").rstrip("/")


def number_index() -> dict[str, str]:
    """phone number -> the simulator's managed-number id."""
    resp = httpx.get(f"{simulator_api()}/api/amd-numbers", timeout=30)
    resp.raise_for_status()
    return {row["phone_number"]: row["id"] for row in resp.json()}


def queue_scenario(scenario: str, number: str, index: dict[str, str]) -> str | None:
    """Queue exactly this scenario on the line's managed endpoint; returns the run ID."""
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
    return ((resp.json() or {}).get("run") or {}).get("id")


def cancel_queued(run_id: str) -> None:
    """Drop an assignment no call will claim, so the line's next call does not inherit it."""
    httpx.post(f"{simulator_api()}/api/runs/{run_id}/cancel", timeout=30)


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
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    log = log or _silent
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

    queued_run = None
    try:
        queued_run = queue_scenario(scenario_name, number, numbers)
        call_id, _ = agent_api.trigger_call(config, scenario=scenario_name, number=number)
    except Exception as exc:  # trigger/queue failures are results, not crashes
        # Only when the agent surely placed no call; after a timeout or 5xx a call may
        # still arrive and must find its scenario.
        if queued_run and isinstance(exc, agent_api.TriggerRejected):
            try:
                cancel_queued(queued_run)
            except httpx.HTTPError:
                pass
        row["error"] = f"{type(exc).__name__}: {exc}"
        # A type and a message alone cannot say WHERE a run died, and a failed
        # suite is expensive to reproduce, so keep the frames too.
        row["traceback"] = traceback.format_exc()[-2000:]
        row["observed_class"] = "unknown"
        row["passed"] = False
        return row

    row["agent_call_id"] = call_id
    log(f"{label} dialing   call_id={call_id}")
    record = None
    try:
        record = agent_api.wait_for_terminal(
            config, call_id, timeout=call_timeout, interval=10.0, label=label, log=log
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


def run_suite(
    config: agent_api.AgentApiConfig,
    plan: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    *,
    out_dir: Path,
    grade_audio: bool,
    log: Callable[[str], None] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run every job across the line pool, one lane per line."""
    log = log or _silent
    jobs = build_jobs(plan)
    total = len(jobs)
    run_id = run_id or new_run_id()
    numbers = plan["sim_numbers"]
    lanes = plan["concurrency"]
    if not numbers or len(numbers) != len(set(numbers)):
        raise ValidationError("a benchmark requires unique simulator numbers")
    if not jobs or lanes < 1 or lanes > len(numbers):
        raise ValidationError("a benchmark requires jobs and one lane per selected number")
    if not config.record_url:
        raise ValidationError("AGENT_API_RECORD_URL is required before placing benchmark calls")
    out_dir.mkdir(parents=True, exist_ok=True)
    number_ids = number_index()
    missing = [n for n in numbers[:lanes] if n not in number_ids]
    if missing:
        raise ValidationError(
            f"not managed by the simulator at {simulator_api()}: {', '.join(missing)}; "
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
                log=log,
            )
            verdict = {True: "PASS", False: "FAIL", None: "INDET"}[row["passed"]]
            failed = [c["check"] for c in row.get("checks") or [] if c["passed"] is False]
            detail = f" failed={','.join(failed)}" if failed else ""
            log(
                f"{label} {verdict} {row['expected_class']}->{row.get('observed_class')} "
                f"{row.get('ended_reason') or '-'}{detail}"
            )
            # Write after every call so an interrupted overnight run still
            # leaves usable evidence.
            with lock:
                rows.append(row)
                report = snapshot()
                write_report(out_dir, report)
            if row.get("agent_terminal") is False:
                # A timed-out call may still occupy the line. Reusing it could
                # consume another scenario's queue entry and corrupt scoring.
                log(f"{label} lane stopped: agent call is not terminal")
                return

    with ThreadPoolExecutor(max_workers=lanes) as pool:
        list(pool.map(lane, numbers[:lanes]))

    return report or snapshot()
