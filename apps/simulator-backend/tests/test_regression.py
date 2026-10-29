import json

import pytest

from telephony_voice_simulator.evaluation import regression
from telephony_voice_simulator.service.errors import ValidationError


def _suite(tmp_path, cases, **extra):
    path = tmp_path / "suite.yaml"
    path.write_text(json.dumps({"version": 1, "persona_line": "+15550100001", **extra,
                                "cases": cases}))
    return path


def test_suite_rejects_unknown_expect_keys_and_incomplete_cases(tmp_path):
    with pytest.raises(ValidationError, match="unknown expect keys"):
        regression.load_suite(_suite(tmp_path, [
            {"name": "a", "kind": "outbound", "persona": "sales-shopper",
             "expect": {"latency_p95": 1}}]))
    with pytest.raises(ValidationError, match="find_command"):
        regression.load_suite(_suite(tmp_path, [
            {"name": "a", "kind": "inbound", "persona": "sales-shopper",
             "agent_number": "+15550100002"}]))
    with pytest.raises(ValidationError, match="target needs"):
        regression.load_suite(_suite(tmp_path, [
            {"name": "a", "kind": "outbound", "persona": "sales-shopper",
             "target": {"line": "+15550100003"}}]))


def test_manual_cases_run_only_when_named(tmp_path):
    suite = regression.load_suite(_suite(tmp_path, [
        {"name": "a", "kind": "outbound", "persona": "sales-shopper"},
        {"name": "b", "kind": "outbound", "persona": "sales-shopper", "manual": True}]))
    assert [c["name"] for c in regression.select(suite, None)] == ["a"]
    assert [c["name"] for c in regression.select(suite, ["b"])] == ["b"]


RECORD = {
    "status": "completed", "endedReason": "transferred", "transferStatus": "bridged",
    "providerMetadata": {"ended_reason": "call.ending.agent-forwarded-call"},
    "tools": ["warm_transfer"], "transcript": "assistant: Connecting you now.",
    "e2e_latency_p50_ms": 900, "e2e_latency_p95_ms": 1500, "duration_ms": 35000,
    "latency": {"llm": {"p95": 800}},
}


def test_record_checks_compare_the_agent_record_with_the_expectation():
    checks, observed = regression._record_checks({
        "status": ["completed"], "transfer_status": ["bridged"], "tools_include": ["warm_transfer"],
        "tools_exclude": ["end_call"], "transcript_contains": ["connecting you"],
        "llm_p95_ms_max": 700, "min_duration_s": 20,
    }, RECORD, regression.DEFAULT_PATHS)
    assert {c["check"]: c["passed"] for c in checks} == {
        "status": True, "transfer_status": True, "tool warm_transfer": True,
        "no tool end_call": True, "says 'connecting you'": True,
        "llm_p95_ms <= 700": False, "duration >= 20s": True,
    }
    assert observed["fine_reason"] == "call.ending.agent-forwarded-call"


def test_missing_latency_is_indeterminate_not_a_pass():
    checks, _ = regression._record_checks({"latency_p95_ms_max": 2000},
                                          {"status": "completed"}, regression.DEFAULT_PATHS)
    row = regression._verdict({"case": "a", "started_at": 0, "checks": checks})
    assert row["passed"] is None


def test_baseline_flags_a_case_that_stopped_passing_or_got_slower():
    baseline = {"cases": [{"case": "a", "passed": True, "observed": {"latency_p95_ms": 1000}}]}
    slow = {"case": "a", "started_at": 0, "passed": True,
            "checks": [{"check": "status", "passed": True, "detail": ""}],
            "observed": {"latency_p95_ms": 1600}}
    regression.compare_baseline([slow], baseline, tolerance=0.3)
    assert slow["passed"] is False
    assert slow["failed_checks"] == ["baseline_latency_p95_ms"]

    fine = {"case": "a", "started_at": 0, "passed": True, "checks": [],
            "observed": {"latency_p95_ms": 1500}}
    regression.compare_baseline([fine], baseline, tolerance=0.3)
    assert fine["passed"] is True


def test_a_failing_before_hook_places_no_call(tmp_path, monkeypatch):
    placed = []
    monkeypatch.setattr(regression, "_run_case", lambda *a: placed.append(a))
    row = regression.run_case({"name": "a", "kind": "outbound", "before": "false",
                               "after": "true"}, {}, None, None, print)
    assert row["passed"] is False and "before command failed" in row["error"]
    assert placed == []
