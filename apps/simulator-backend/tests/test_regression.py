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


def test_an_unreached_target_is_cancelled_and_an_ungraded_leg_fails(monkeypatch):
    from telephony_voice_simulator.evaluation.benchmark import execution

    monkeypatch.setattr(execution, "find_sim_call", lambda *a, **k: {"id": "CA1", "scenario": "s"})
    checks, leg = regression._target_checks({"line": "+15550100003", "scenario": "s"}, 0)
    assert leg["id"] == "CA1"
    assert {c["check"]: c["passed"] for c in checks}["target_graded"] is False


def test_metrics_time_the_transfer_from_the_agent_timeline_and_the_target_leg():
    record = {"timeline": {"first_agent_word_s": 1.2,
                           "transfer_started_at": "2026-11-01T12:00:00+00:00",
                           "consult_started_at": "2026-11-01T12:00:09+00:00",
                           "briefing_ended_at": "2026-11-01T12:00:15.5+00:00",
                           "hold_ms": 21000}}

    from types import SimpleNamespace

    persona = SimpleNamespace(analysis={"customer_dialogue": [
        {"role": "assistant", "content": "hi", "t": 0},
        {"role": "user", "content": "Thanks for calling", "t": 2}]})
    leg = {"started_at": "2026-11-01T12:00:06+00:00", "from_address": "+15550100001"}
    m = regression._metrics(record, {"latency_p50_ms": 900.0, "latency_p95_ms": 1500.0}, persona, leg)
    assert m["ttfw_customer_s"] == 2 and m["ttfw_agent_s"] == 1.2
    assert m["transfer_setup_s"] == 6.0 and m["consult_after_s"] == 9.0
    assert m["hold_s"] == 21.0 and m["briefing_s"] == 6.5
    assert m["turn_p95_ms"] == 1500.0 and m["target_caller_id"] == "+15550100001"
    assert regression._metrics(None, {}, None, None)["transfer_setup_s"] is None
    # Epoch seconds and offset-less ISO times (read as UTC) work too.
    assert regression._seconds_between(1793534400, "2026-11-01T12:00:05") == 5.0
    assert regression._seconds_between("not a time", 5) is None


def test_metric_limits_must_be_numbers(tmp_path):
    with pytest.raises(ValidationError, match="ttfw_s_max must be a number"):
        regression.load_suite(_suite(tmp_path, [{"name": "a", "kind": "outbound", "persona": "p",
                                                  "expect": {"ttfw_s_max": "5"}}]))


def test_metric_checks_cover_caller_id_completeness_and_limits():
    suite = {"persona_line": "+15550100001", "complete_fields": ["status", "recording_url"]}
    case = {"agent_number": "+15550100002"}
    metrics = {"ttfw_customer_s": 5.0, "transfer_setup_s": None, "target_caller_id": "+15550100002"}
    record = {"status": "completed", "recording_url": "", "extra": {"x": 1}}
    checks = {c["check"]: c["passed"] for c in regression._metric_checks(
        {"ttfw_s_max": 4, "transfer_setup_s_max": 10, "target_caller_id": ["customer"],
         "complete": ["extra.x"]}, metrics, case, suite, record)}
    assert checks == {"ttfw_customer_s <= 4": False, "transfer_setup_s <= 10": None,
                      "target_caller_id": False, "call_ended_complete": False}
    record["recording_url"] = "https://rec"
    checks = {c["check"]: c["passed"] for c in regression._metric_checks(
        {"target_caller_id": ["customer", "agent"], "complete": True}, metrics, case, suite, record)}
    assert checks == {"target_caller_id": True, "call_ended_complete": True}


def test_a_crashing_case_becomes_a_failed_row_and_the_report_is_kept(tmp_path, monkeypatch):
    suite = _suite(tmp_path, [
        {"name": "a", "kind": "outbound", "persona": "sales-shopper"},
        {"name": "b", "kind": "outbound", "persona": "sales-shopper"}],
        defaults={"pace_seconds": 0})
    monkeypatch.setenv("AGENT_API_TRIGGER_URL", "https://agent.example/trigger")
    body = tmp_path / "body.json"
    body.write_text("{}")
    monkeypatch.setenv("AGENT_API_BODY", str(body))
    monkeypatch.setenv("AGENT_API_RECORD_COMMAND", "true {call_id}")

    def boom(case, *_a):
        if case["name"] == "a":
            raise RuntimeError("simulator down")
        return regression._verdict({"case": "b", "started_at": 0,
                                    "checks": [{"check": "x", "passed": True}]})

    monkeypatch.setattr(regression, "run_case", boom)
    report = regression.run(suite_path=suite, store=None, out_dir=tmp_path / "out", log=lambda _l: None)
    assert [r["passed"] for r in report["cases"]] == [False, True]
    assert "simulator down" in report["cases"][0]["error"]
    assert json.loads((tmp_path / "out" / f"{report['report_id']}.json").read_text())["failed"] == 1
