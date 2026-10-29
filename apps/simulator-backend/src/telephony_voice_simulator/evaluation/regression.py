"""Regression suite: real calls against a deployed agent, pass/fail per case, and a baseline.

One suite YAML names the cases an agent change must not break. Each case places one real
call and asserts on the agent's own call record (status, end reason, transfer outcome,
tools, latency), plus the simulator's evidence when the case has it:

  amd       the agent calls a scripted machine/human line; the scenario's own
            expectations and audio checks grade it (the AMD benchmark's run_once)
  outbound  the agent calls a persona line (POST trigger with the case's body)
  inbound   a persona line calls the agent's inbound number (``send_digits`` for a menu)

Either persona kind may name a ``target``: a transfer-target scenario queued on the line
the agent transfers to. The target line's checks and the persona's grade (code words heard
only after a bridge) then prove the transfer, beside the agent's ``transfer_status``.

Suite shape::

    version: 1
    name: spyne-uat
    persona_line: "+15550100001"
    find_command: "python3 find-call {customer} {agent} {since}"   # inbound cases only
    defaults: {timeout_seconds: 420}
    cases:
      - name: warm_transfer_accept
        kind: inbound
        agent_number: "+15550100002"
        persona: transfer-to-service
        send_digits: "wwww4"
        target: {line: "+15550100003", scenario: transfer_target_warm_accept}
        expect:
          status: [completed]
          ended_reason: [transferred]
          transfer_status: [bridged]
          latency_p95_ms_max: 3000

A case may run ``before`` and ``after`` commands (shell words, no shell), e.g. to switch
the agent's transfer type for one case and back; ``after`` runs even when the case fails,
and a failing ``before`` fails the case without placing a call.

``expect`` keys: status, ended_reason, fine_reason (dotted path from
``fine_reason_path``), transfer_status, tools_include, tools_exclude,
transcript_contains, transcript_excludes, latency_p50_ms_max, latency_p95_ms_max (end of the
user's turn to agent audio, tool time included), llm_p95_ms_max, tts_p95_ms_max,
min_duration_s, persona (pass|skip), target (pass|skip).

The agent API (trigger URL, authorization, record source) comes from the
``AGENT_API_*`` environment, as for the benchmark; a case's ``body`` replaces the body
template. A baseline is a previous report: each case must still pass and its p95
latency must stay within ``tolerance`` of the baseline's.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from ..integrations import agent_api
from ..service.errors import ValidationError
from .benchmark import execution
from .benchmark.plan import load_scenarios

KINDS = ("amd", "outbound", "inbound")
EXPECT_KEYS = {
    "status", "ended_reason", "fine_reason", "transfer_status", "tools_include",
    "tools_exclude", "transcript_contains", "transcript_excludes", "latency_p50_ms_max",
    "latency_p95_ms_max", "llm_p95_ms_max", "tts_p95_ms_max", "min_duration_s", "persona",
    "target",
}
# Paths into the agent's record; overridable per suite for another platform's shape.
DEFAULT_PATHS = {
    "fine_reason_path": "providerMetadata.ended_reason",
    "transfer_status_path": "transferStatus",
    "tools_path": "tools",
    "transcript_path": "transcript",
    "latency_p50_path": "e2e_latency_p50_ms",
    "latency_p95_path": "e2e_latency_p95_ms",
    "llm_p95_path": "latency.llm.p95",
    "tts_p95_path": "latency.tts.p95",
    "duration_ms_path": "duration_ms",
}
Log = Callable[[str], None]


def load_suite(path: str | Path) -> dict[str, Any]:
    suite = yaml.safe_load(Path(path).read_text()) or {}
    if suite.get("version") != 1 or not isinstance(suite.get("cases"), list):
        raise ValidationError("a regression suite needs version: 1 and a cases list")
    names = set()
    scenarios = None
    for case in suite["cases"]:
        name = case.get("name")
        if not name or name in names:
            raise ValidationError(f"every case needs a unique name (got {name!r})")
        names.add(name)
        kind = case.get("kind")
        if kind not in KINDS:
            raise ValidationError(f"{name}: kind must be one of {', '.join(KINDS)}")
        if unknown := set(case.get("expect") or {}) - EXPECT_KEYS:
            raise ValidationError(f"{name}: unknown expect keys {sorted(unknown)}")
        if kind == "amd":
            scenarios = scenarios or load_scenarios()
            if case.get("scenario") not in scenarios or not case.get("line"):
                raise ValidationError(f"{name}: amd cases need a known scenario and a line")
        else:
            if not case.get("persona"):
                raise ValidationError(f"{name}: persona cases need a persona")
            if not suite.get("persona_line"):
                raise ValidationError("persona cases need the suite's persona_line")
        if kind == "inbound" and not (case.get("agent_number") and suite.get("find_command")):
            raise ValidationError(f"{name}: inbound cases need agent_number and find_command")
        target = case.get("target")
        if target is not None and not (isinstance(target, dict) and target.get("line")
                                       and target.get("scenario")):
            raise ValidationError(f"{name}: target needs line and scenario")
    return suite


def select(suite: dict[str, Any], names: list[str] | None) -> list[dict[str, Any]]:
    cases = suite["cases"]
    if not names:
        return [c for c in cases if not c.get("manual")]
    known = {c["name"]: c for c in cases}
    if missing := [n for n in names if n not in known]:
        raise ValidationError(f"unknown cases: {', '.join(missing)}")
    return [known[n] for n in names]


def _config(case: dict[str, Any], base: agent_api.AgentApiConfig) -> agent_api.AgentApiConfig:
    if not case.get("body"):
        return base
    body = json.loads(Path(case["body"]).expanduser().read_text())
    body.pop("_comment", None)
    return replace(base, body_template=body)


def _check(name: str, passed: bool | None, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": passed, "detail": detail}


def _record_checks(expect: dict[str, Any], record: dict[str, Any] | None,
                   paths: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def get(key: str) -> Any:
        return agent_api.dotted(record or {}, paths[key])

    status = str(agent_api.dotted(record or {}, "status") or "")
    observed = {
        "status": status,
        "ended_reason": str(agent_api.dotted(record or {}, "endedReason") or ""),
        "fine_reason": str(get("fine_reason_path") or ""),
        "transfer_status": str(get("transfer_status_path") or ""),
        "tools": list(get("tools_path") or []),
        "latency_p50_ms": get("latency_p50_path"),
        "latency_p95_ms": get("latency_p95_path"),
        "llm_p95_ms": get("llm_p95_path"),
        "tts_p95_ms": get("tts_p95_path"),
        "duration_s": round((get("duration_ms_path") or 0) / 1000, 1),
    }
    transcript = str(get("transcript_path") or "").lower()
    checks = []
    for key in ("status", "ended_reason", "fine_reason", "transfer_status"):
        if key in expect:
            want = expect[key] if isinstance(expect[key], list) else [expect[key]]
            checks.append(_check(key, observed[key] in want, f"want {want} got {observed[key]!r}"))
    for word in expect.get("tools_include") or []:
        checks.append(_check(f"tool {word}", word in observed["tools"], f"tools={observed['tools']}"))
    for word in expect.get("tools_exclude") or []:
        checks.append(_check(f"no tool {word}", word not in observed["tools"],
                             f"tools={observed['tools']}"))
    for phrase in expect.get("transcript_contains") or []:
        checks.append(_check(f"says {phrase!r}", phrase.lower() in transcript, "agent transcript"))
    for phrase in expect.get("transcript_excludes") or []:
        checks.append(_check(f"never {phrase!r}", phrase.lower() not in transcript, "agent transcript"))
    for key in ("latency_p50_ms", "latency_p95_ms", "llm_p95_ms", "tts_p95_ms"):
        if (limit := expect.get(f"{key}_max")) is not None:
            value = observed[key]
            checks.append(_check(f"{key} <= {limit}", None if value is None else value <= limit,
                                 f"{key}={value}"))
    if (floor := expect.get("min_duration_s")) is not None:
        checks.append(_check(f"duration >= {floor}s", observed["duration_s"] >= floor,
                             f"duration_s={observed['duration_s']}"))
    return checks, observed


def _find_inbound(suite: dict[str, Any], agent_number: str, since: float, timeout: float) -> str:
    argv = [a.format(customer=suite["persona_line"], agent=agent_number, since=int(since))
            for a in shlex.split(suite["find_command"])]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0]
        time.sleep(10)
    return ""


def _persona_call(store: Any, line: str, since: float, timeout: float) -> Any:
    """The persona leg on ``line`` that started after ``since`` (the newest one)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for call in store.list_incoming_calls(limit=20):
            started = execution._parse_iso(call.started_at)
            if started and started >= since - 2 and "customer_dialogue" in (call.analysis or {}) \
                    and line in (call.from_address, call.to_address):
                return call
        time.sleep(5)
    return None


def _grade_persona(store: Any, line: str, since: float, log: Log) -> tuple[dict[str, Any], str]:
    from ..service import customer

    call = _persona_call(store, line, since, timeout=60)
    if call is None:
        return _check("persona_leg_present", False, f"no persona call on {line}"), ""
    try:
        grade = customer.grade_session(store, call.id, wait=True, progress=log)
    except Exception as exc:  # a grading failure is a result, not a crash
        return _check("persona_graded", False, f"{type(exc).__name__}: {exc}"), call.id
    failed = [c.get("check") for c in grade.get("checks", []) if not c.get("passed")]
    failed += [e.get("expectation") or e.get("name") for e in grade.get("expectations", [])
               if not e.get("passed")]
    return _check("persona_grade", bool(grade.get("passed")),
                  "passed" if grade.get("passed") else f"failed: {failed}"), call.id


def _target_checks(target: dict[str, Any], since: float) -> tuple[list[dict[str, Any]], str]:
    leg = execution.find_sim_call(target["line"], since, timeout=120)
    if leg is None:
        return [_check("target_reached", False, f"the agent never called {target['line']}")], ""
    analysis = leg.get("analysis") or {}
    checks = [_check("target_reached", True, f"leg {leg.get('id')}"),
              _check("target_scenario", leg.get("scenario") == target["scenario"],
                     f"want {target['scenario']} got {leg.get('scenario')}")]
    checks += [{**c, "check": f"target {c['check']}"} for c in analysis.get("checks") or []]
    return checks, str(leg.get("id") or "")


def _hook(case: dict[str, Any], key: str) -> str | None:
    if not case.get(key):
        return None
    out = subprocess.run(shlex.split(case[key]), capture_output=True, text=True, timeout=120)
    return None if out.returncode == 0 else f"{key} command failed: {out.stderr.strip()[-300:]}"


def run_case(case: dict[str, Any], suite: dict[str, Any], base: agent_api.AgentApiConfig,
             store: Any, log: Log) -> dict[str, Any]:
    if failure := _hook(case, "before"):
        _hook(case, "after")
        return _verdict({"case": case["name"], "kind": case["kind"], "started_at": time.time(),
                         "error": failure, "checks": []})
    try:
        return _run_case(case, suite, base, store, log)
    finally:
        if failure := _hook(case, "after"):
            log(f"{case['name']}: {failure}")


def _run_case(case: dict[str, Any], suite: dict[str, Any], base: agent_api.AgentApiConfig,
              store: Any, log: Log) -> dict[str, Any]:
    defaults = suite.get("defaults") or {}
    timeout = float(case.get("timeout_seconds") or defaults.get("timeout_seconds") or 420)
    paths = {**DEFAULT_PATHS, **{k: v for k, v in suite.items() if k in DEFAULT_PATHS}}
    config = _config(case, base)
    expect = case.get("expect") or {}
    row: dict[str, Any] = {"case": case["name"], "kind": case["kind"], "started_at": time.time()}
    started = row["started_at"]

    if case["kind"] == "amd":
        result = execution.run_once(
            config, scenario_name=case["scenario"], scenario=load_scenarios()[case["scenario"]],
            number=case["line"], attempt=1, call_timeout=timeout, grade_audio=True,
            numbers=execution.number_index(), label=case["name"], log=log,
        )
        record = agent_api.fetch_record(config, result["agent_call_id"]) \
            if result.get("agent_call_id") else None
        checks, observed = _record_checks(expect, record, paths)
        row.update(call_id=result.get("agent_call_id"), sim_call_id=result.get("sim_call_sid"),
                   observed={**observed, "amd": result.get("amd")},
                   checks=result.get("checks", []) + checks, error=result.get("error"))
        return _verdict(row)

    from ..service import customer

    line = suite["persona_line"]
    target = case.get("target")
    checks: list[dict[str, Any]] = []
    queued_target = None
    try:
        if waiting := store.list_customer_persona_queue(line):
            for item in waiting:  # left over from an aborted run; it would take this call
                store.cancel_customer_persona(item["id"])
        if target:
            queued_target = execution.queue_scenario(
                target["scenario"], target["line"], execution.number_index())
        if case["kind"] == "outbound":
            customer.queue_persona(store, line, persona_id=case["persona"], label=case["name"])
            call_id, _ = agent_api.trigger_call(config, scenario=case["name"], number=line)
        else:
            dialed = customer.dial(store, line, case["agent_number"], persona_id=case["persona"],
                                   label=case["name"], send_digits=case.get("send_digits", ""))
            row["persona_call_id"] = dialed["call_id"]
            call_id = _find_inbound(suite, case["agent_number"], started, timeout=120)
            if not call_id:
                raise RuntimeError("the agent never logged the inbound call")
    except Exception as exc:  # setup and trigger failures are results
        row["error"] = f"{type(exc).__name__}: {exc}"
        if queued_target:
            execution.cancel_queued(queued_target)
        row["checks"] = []
        return _verdict(row)

    row["call_id"] = call_id
    log(f"{case['name']} call_id={call_id}")
    record = agent_api.wait_for_terminal(config, call_id, timeout=timeout, label=case["name"],
                                         log=log)
    checks.append(_check("agent_call_terminal", agent_api.is_terminal(config, record),
                         f"status={agent_api.dotted(record or {}, 'status')}"))
    record_checks, row["observed"] = _record_checks(expect, record, paths)
    checks += record_checks
    if expect.get("persona", "pass") == "pass":
        check, row["persona_call_id"] = _grade_persona(store, line, started, log)
        checks.append(check)
    if target:
        target_checks, row["target_call_id"] = _target_checks(target, started)
        if expect.get("target", "pass") == "pass":
            checks += target_checks
        elif queued_target and not row["target_call_id"]:
            execution.cancel_queued(queued_target)
    row["checks"] = checks
    return _verdict(row)


def _verdict(row: dict[str, Any]) -> dict[str, Any]:
    checks = row.get("checks") or []
    if row.get("error") or any(c["passed"] is False for c in checks):
        row["passed"] = False
    elif not checks or any(c["passed"] is None for c in checks):
        row["passed"] = None
    else:
        row["passed"] = True
    row["failed_checks"] = [c["check"] for c in checks if c["passed"] is False]
    row["duration_s"] = round(time.time() - row["started_at"], 1)
    return row


def compare_baseline(rows: list[dict[str, Any]], baseline: dict[str, Any],
                     tolerance: float) -> None:
    """Add a baseline check to each row: still passing, p95 within tolerance (+250 ms)."""
    previous = {r["case"]: r for r in baseline.get("cases", [])}
    for row in rows:
        before = previous.get(row["case"])
        if before is None:
            continue
        checks = row.setdefault("checks", [])
        if before.get("passed") is True:
            checks.append(_check("baseline_still_passes", row["passed"] is True,
                                 f"baseline passed, now {row['passed']}"))
        for key in ("latency_p95_ms", "llm_p95_ms"):
            old = (before.get("observed") or {}).get(key)
            new = (row.get("observed") or {}).get(key)
            if old is not None and new is not None:
                limit = old * (1 + tolerance) + 250
                checks.append(_check(f"baseline_{key}", new <= limit,
                                     f"{new:.0f} ms vs baseline {old:.0f} ms (limit {limit:.0f})"))
        _verdict(row)


def run(*, suite_path: str | Path, cases: list[str] | None = None, store: Any,
        baseline: str | Path | None = None, tolerance: float = 0.3,
        out_dir: str | Path = "results/regression", log: Log = print) -> dict[str, Any]:
    suite = load_suite(suite_path)
    selected = select(suite, cases)
    base = agent_api.load_config()
    if not base.can_read_records:
        raise ValidationError("set AGENT_API_RECORD_URL or AGENT_API_RECORD_COMMAND")
    report_id = f"regress-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    rows = []
    pace = float((suite.get("defaults") or {}).get("pace_seconds", 10))
    for index, case in enumerate(selected):
        if index:
            time.sleep(pace)
        log(f"[{index + 1}/{len(selected)}] {case['name']} ({case['kind']})")
        row = run_case(case, suite, base, store, log)
        verdict = {True: "PASS", False: "FAIL", None: "INDET"}[row["passed"]]
        log(f"  {verdict} {case['name']} call_id={row.get('call_id')} "
            f"failed={row['failed_checks']}")
        rows.append(row)
    if baseline:
        compare_baseline(rows, json.loads(Path(baseline).read_text()), tolerance)
    report = {
        "report_id": report_id, "suite": suite.get("name") or str(suite_path),
        "baseline": str(baseline) if baseline else None,
        "passed": sum(r["passed"] is True for r in rows),
        "failed": sum(r["passed"] is False for r in rows),
        "indeterminate": sum(r["passed"] is None for r in rows),
        "ok": all(r["passed"] is True for r in rows), "cases": rows,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{report_id}.json").write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(out / f"{report_id}.json")
    return report


def summary_table(report: dict[str, Any]) -> str:
    lines = ["| case | result | call id | status / reason | transfer | e2e p50 / p95 ms "
             "| llm p95 ms | failed checks |",
             "|---|---|---|---|---|---|---|---|"]
    for r in report["cases"]:
        o = r.get("observed") or {}
        verdict = {True: "PASS", False: "FAIL", None: "INDET"}[r["passed"]]
        p50, p95 = o.get("latency_p50_ms"), o.get("latency_p95_ms")
        lat = f"{p50:.0f} / {p95:.0f}" if p50 is not None and p95 is not None else ""
        lines.append(
            f"| {r['case']} | {verdict} | {r.get('call_id') or ''} | {o.get('status', '')} / "
            f"{o.get('fine_reason') or o.get('ended_reason', '')} | {o.get('transfer_status', '')} "
            f"| {lat} | {o.get('llm_p95_ms') if o.get('llm_p95_ms') is not None else ''} "
            f"| {', '.join(r['failed_checks']) or (r.get('error') or '')} |"
        )
    return "\n".join(lines)
