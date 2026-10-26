"""Scoring: callee classes, agent-record checks, aggregation, rescoring, preflight health."""

from __future__ import annotations

import math
import statistics
from typing import Any

# End reasons that mean "the agent concluded it was talking to a machine".
MACHINE_ENDED_PREFIXES = ("call.ending.voicemail", "call.ending.machine")
UNCONNECTED_STATUSES = {"failed", "no-answer", "no_answer", "busy", "canceled", "cancelled"}


def expected_class(scenario: dict[str, Any]) -> str:
    """What is on the line: machine, human, screener, or unknown.

    Derived from the scenario's own expectations so the catalog stays the
    single source of truth. An explicit ``expect.callee_class`` wins when a
    scenario is too unusual to classify from its assertions.

    ``screener`` is its own class on purpose. A gate or call-screening
    assistant IS a machine that the agent must detect and navigate, but the
    call must still end as a live conversation, so scoring it as either plain
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
    to fire (that is the machine gate) and the agent is then supposed to
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
    NOT gate on the scenario's assertions: an agent that genuinely fails
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
