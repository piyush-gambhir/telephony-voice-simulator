from __future__ import annotations

import argparse
import json
import os
import subprocess

import pytest

from telephony_voice_simulator import benchmark
from telephony_voice_simulator.agent_api import AgentApiConfig


def test_expected_class_covers_every_catalog_scenario() -> None:
    """An unclassifiable scenario silently poisons the confusion matrix."""
    catalog = benchmark.load_scenarios()
    assert len(catalog) >= 30
    unknown = {
        name
        for name, scenario in catalog.items()
        if benchmark.expected_class(scenario) == "unknown"
    }
    assert not unknown, f"scenarios need an explicit expect.callee_class: {sorted(unknown)}"


def test_expected_class_derivations() -> None:
    machine = {"expect": {"ended_reason": "call.ending.voicemail-left-message"}}
    assert benchmark.expected_class(machine) == "machine"

    dead_end = {
        "expect": {
            "ended_reason_any_of": [
                "call.ending.voicemail-hangup",
                "call.ending.voicemail-inbox-full",
            ],
            "ended_reason_not": ["call.ending.voicemail-left-message"],
        }
    }
    assert benchmark.expected_class(dead_end) == "machine"

    human = {"expect": {"detection_layer_absent": True}}
    assert benchmark.expected_class(human) == "human"

    live = {"expect": {"ended_reason_not": ["call.ending.voicemail-left-message"]}}
    assert benchmark.expected_class(live) == "human"

    screener = {
        "expect": {
            "detection_layer_prefix_any_of": ["livekit-amd"],
            "ended_reason_not": ["call.ending.voicemail-left-message"],
        }
    }
    assert benchmark.expected_class(screener) == "screener"

    assert benchmark.expected_class({"expect": {"callee_class": "machine"}}) == "machine"
    assert benchmark.expected_class({"expect": {"agent_spoke": True}}) == "unknown"


def test_observed_class_is_decided_by_the_ending() -> None:
    # Detection with no ending says nothing about what the agent DID.
    assert benchmark.observed_class({"is_machine": True, "ended_reason": ""}) == "unknown"
    assert (
        benchmark.observed_class(
            {"is_machine": None, "ended_reason": "call.ending.voicemail-hangup"}
        )
        == "machine"
    )
    assert (
        benchmark.observed_class(
            {"is_machine": False, "ended_reason": "call.ending.customer-ended-call"}
        )
        == "human"
    )
    assert benchmark.observed_class({"is_machine": None, "ended_reason": ""}) == "unknown"


def test_record_checks_grade_the_agent_reported_outcome() -> None:
    expect = {
        "webhook_received": True,
        "ended_reason": "call.ending.voicemail-left-message",
        "detection_layer_prefix_any_of": ["livekit-amd", "phrase-fallback"],
        "ended_reason_not": ["call.ending.machine-ivr"],
    }
    outcome = {
        "status": "completed",
        "ended_reason": "call.ending.voicemail-left-message",
        "detection_layer": "livekit-amd:llm",
    }
    checks = {c["check"]: c["passed"] for c in benchmark.record_checks(expect, outcome)}
    assert checks == {
        "agent_record_received": True,
        "ended_reason": True,
        "ended_reason_not": True,
        "detection_layer": True,
    }

    wrong = dict(outcome, ended_reason="call.ending.machine-ivr", detection_layer="")
    failed = {c["check"]: c["passed"] for c in benchmark.record_checks(expect, wrong)}
    assert failed["ended_reason"] is False
    assert failed["ended_reason_not"] is False
    assert failed["detection_layer"] is False


def _row(scenario: str, expected: str, observed: str, passed, delay=None) -> dict:
    return {
        "scenario": scenario,
        "attempt": 1,
        "expected_class": expected,
        "observed_class": observed,
        "passed": passed,
        "detection_delay": delay,
        "checks": [{"check": "ended_reason", "passed": passed, "detail": "d"}],
        "error": None,
    }


def test_summarize_builds_the_matrix_and_error_rates() -> None:
    rows = [
        _row("vm_a", "machine", "machine", True, 0.7),
        _row("vm_a", "machine", "human", False, 2.1),
        _row("human_a", "human", "machine", False, 1.0),
        _row("screen_a", "screener", "human", True, 1.4),
    ]
    summary = benchmark.summarize(rows)
    assert summary["calls"] == 4
    assert summary["passed"] == 2
    assert summary["failed"] == 2
    assert summary["confusion_matrix"]["machine"] == {"machine": 1, "human": 1}
    # 1 of 2 machine calls was read as a human.
    assert summary["false_human_rate"] == 0.5
    # human + screener are both "live"; only the human row was misread.
    assert summary["false_machine_rate"] == 0.5
    assert summary["per_scenario"]["vm_a"]["attempts"] == 2
    assert summary["per_scenario"]["vm_a"]["pass_rate"] == 0.5
    assert summary["detection_delay"]["n"] == 4


def test_summarize_treats_indeterminate_rows_as_neither() -> None:
    summary = benchmark.summarize([_row("a", "machine", "machine", None)])
    assert summary["indeterminate"] == 1
    assert summary["pass_rate"] is None


def test_render_markdown_includes_the_headline_numbers() -> None:
    rows = [_row("vm_a", "machine", "human", False, 2.0)]
    report = {
        "run_id": "bench-test",
        "suite": "unit",
        "sim_numbers": ["+15550100000", "+15550100001"],
        "rows": rows,
        "summary": benchmark.summarize(rows),
    }
    text = benchmark.render_markdown(report)
    assert "# AMD benchmark — unit" in text
    assert "`+15550100000`, `+15550100001`" in text
    assert "machine scored as human: 100.0%" in text
    assert "vm_a" in text
    assert "## Failures" in text


def _args(**overrides) -> argparse.Namespace:
    base = {
        "suite": None,
        "scenario": None,
        "repeat": None,
        "pace": None,
        "number": None,
        "concurrency": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _api_config() -> AgentApiConfig:
    return AgentApiConfig(
        "https://agent.test/calls", {}, record_url="https://agent.test/calls/{call_id}"
    )


def test_resolve_plan_defaults_and_overrides() -> None:
    catalog = benchmark.load_scenarios()
    plan = benchmark.resolve_plan(_args(scenario=["mailbox_full"], repeat=4), catalog)
    assert plan == {
        "name": "ad-hoc",
        "scenarios": ["mailbox_full"],
        "repeat": 4,
        "pace_seconds": 15.0,
        "call_timeout_seconds": 300.0,
        "sim_numbers": [],
        "concurrency": 1,
    }


def test_resolve_plan_gives_one_lane_per_line() -> None:
    catalog = benchmark.load_scenarios()
    lines = ["+15550100000", "+15550100001", "+15550100002"]
    plan = benchmark.resolve_plan(_args(scenario=["mailbox_full"], number=lines), catalog)
    assert plan["sim_numbers"] == lines
    assert plan["concurrency"] == 3

    # Concurrency can never exceed the pool: two lanes on one line would race
    # for that line's scenario queue.
    capped = benchmark.resolve_plan(
        _args(scenario=["mailbox_full"], number=lines[:2], concurrency=9), catalog
    )
    assert capped["concurrency"] == 2

    fewer = benchmark.resolve_plan(
        _args(scenario=["mailbox_full"], number=lines, concurrency=2), catalog
    )
    assert fewer["concurrency"] == 2


def test_build_jobs_is_attempt_major() -> None:
    plan = {"scenarios": ["a", "b"], "repeat": 3}
    jobs = benchmark.build_jobs(plan)
    assert len(jobs) == 6
    # Every scenario is covered once before any is covered twice, so an
    # aborted run still has balanced coverage.
    assert jobs[:2] == [("a", 1), ("b", 1)]
    assert jobs[2:4] == [("a", 2), ("b", 2)]


def test_render_comparison_flags_divergence() -> None:
    uat_rows = [_row("vm_a", "machine", "machine", True, 0.7)]
    prod_rows = [_row("vm_a", "machine", "human", False, 2.4)]
    reports = [
        ("uat", {"summary": benchmark.summarize(uat_rows), "rows": uat_rows}),
        ("prod", {"summary": benchmark.summarize(prod_rows), "rows": prod_rows}),
    ]
    text = benchmark.render_comparison(reports)
    assert "| metric | uat | prod |" in text
    assert "## Diverged" in text
    assert "uat=100.0%" in text and "prod=0.0%" in text


def test_resolve_plan_rejects_unknown_scenarios() -> None:
    catalog = benchmark.load_scenarios()
    try:
        benchmark.resolve_plan(_args(scenario=["not_a_scenario"]), catalog)
    except SystemExit as exit_error:
        assert "not_a_scenario" in str(exit_error)
    else:  # pragma: no cover - the call must not succeed
        raise AssertionError("unknown scenario was accepted")


def test_example_suite_is_valid(tmp_path) -> None:
    from telephony_voice_simulator.paths import BACKEND_ROOT

    suite = BACKEND_ROOT / "config" / "benchmark.example.yaml"
    plan = benchmark.resolve_plan(_args(suite=str(suite)), benchmark.load_scenarios())
    assert plan["name"] == "amd-core"
    assert plan["repeat"] >= 1
    assert len(plan["scenarios"]) >= 10


def test_run_suite_passes_the_number_index_to_every_lane(monkeypatch, tmp_path) -> None:
    """Regression: the lane's job counter once shadowed the number index.

    run_once then received an int where it expected {phone: number_id}, and
    every call died in queue_scenario with an AttributeError — after the
    carrier leg had already been paid for.
    """
    seen: list[object] = []

    monkeypatch.setattr(benchmark, "number_index", lambda: {"+15550100000": "number_abc"})

    def fake_run_once(config, **kwargs):
        seen.append(kwargs["numbers"])
        return _row(kwargs["scenario_name"], "machine", "machine", True)

    monkeypatch.setattr(benchmark, "run_once", fake_run_once)

    plan = {
        "name": "unit",
        "scenarios": ["mailbox_full"],
        "repeat": 2,
        "pace_seconds": 0,
        "call_timeout_seconds": 30.0,
        "sim_numbers": ["+15550100000"],
        "concurrency": 1,
    }
    report = benchmark.run_suite(
        _api_config(),
        plan,
        benchmark.load_scenarios(),
        out_dir=tmp_path,
        grade_audio=False,
    )
    assert seen == [{"+15550100000": "number_abc"}] * 2
    assert report["summary"]["calls"] == 2
    assert (tmp_path / "report.json").exists()


def test_run_suite_refuses_lines_the_simulator_does_not_manage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(benchmark, "number_index", lambda: {"+15550100000": "number_abc"})
    plan = {
        "name": "unit",
        "scenarios": ["mailbox_full"],
        "repeat": 1,
        "pace_seconds": 0,
        "call_timeout_seconds": 30.0,
        "sim_numbers": ["+15550100000", "+15550109999"],
        "concurrency": 2,
    }
    try:
        benchmark.run_suite(
            _api_config(), plan, benchmark.load_scenarios(), out_dir=tmp_path, grade_audio=False
        )
    except SystemExit as exit_error:
        assert "+15550109999" in str(exit_error)
    else:  # pragma: no cover
        raise AssertionError("unmanaged line was accepted")


def _preflight_report(**row_overrides) -> dict:
    row = {
        "scenario": "stock_voicemail_beep_1000",
        "agent_call_id": "019f-abc",
        "ended_reason": "call.ending.voicemail-left-message",
        "sim_call_sid": "CA123",
        "status": "completed",
        "error": None,
        "checks": [{"check": "message_start_after", "passed": True, "detail": "d"}],
    }
    row.update(row_overrides)
    return {"rows": [row]}


def test_preflight_passes_when_the_agent_fails_but_the_pipeline_worked() -> None:
    """A failing agent is the result we came for, not a reason to refuse to run."""
    report = _preflight_report(
        checks=[{"check": "message_start_after", "passed": False, "detail": "5.36s"}]
    )
    healthy, detail = benchmark.preflight_health(report)
    assert healthy is True
    assert "message_start_after" in detail


def test_preflight_fails_on_each_broken_link() -> None:
    for override, expected in [
        ({"error": "RuntimeError: trigger failed 401: ..."}, "401"),
        ({"agent_call_id": None}, "call id"),
        ({"ended_reason": ""}, "end reason"),
        ({"sim_call_sid": None}, "never reached the simulator"),
        ({"checks": [{"check": "x", "passed": None, "detail": "d"}]}, "nothing was graded"),
    ]:
        healthy, detail = benchmark.preflight_health(_preflight_report(**override))
        assert healthy is False, override
        assert expected in detail, (override, detail)

    healthy, detail = benchmark.preflight_health({"rows": []})
    assert healthy is False and "no call" in detail


def test_observed_class_does_not_punish_a_navigated_screener() -> None:
    """Detection firing on a gate is CORRECT; the ending decides the verdict."""
    navigated = {
        "ended_reason": "call.in-progress.customer-ended-call",
        "is_machine": True,
        "detection_layer": "livekit-amd:dtmf-nav:5",
    }
    assert benchmark.observed_class(navigated) == "screener"

    # Same detection, but the agent gave up and left a voicemail.
    gave_up = dict(navigated, ended_reason="call.ending.voicemail-left-message")
    assert benchmark.observed_class(gave_up) == "machine"

    # Live pickup with no detection at all is a plain human.
    human = {
        "ended_reason": "call.in-progress.customer-ended-call",
        "is_machine": False,
        "detection_layer": "",
    }
    assert benchmark.observed_class(human) == "human"

    assert benchmark.observed_class({"ended_reason": "", "is_machine": None}) == "unknown"


def test_rescore_report_reclassifies_without_redialling() -> None:
    stored = {
        "run_id": "bench-x",
        "suite": "unit",
        "sim_numbers": ["+15550100000"],
        "rows": [
            {
                "scenario": "dtmf_gate_honored",
                "attempt": 1,
                "expected_class": "screener",
                "observed_class": "machine",  # the old, wrong verdict
                "ended_reason": "call.in-progress.customer-ended-call",
                "detection_layer": "livekit-amd:dtmf-nav:5",
                "amd": {"is_machine": True},
                "detection_delay": 0.8,
                "passed": True,
                "checks": [{"check": "dtmf_received", "passed": True, "detail": "d"}],
                "error": None,
            }
        ],
    }
    stored["summary"] = benchmark.summarize(stored["rows"])
    assert stored["summary"]["confusion_matrix"]["screener"] == {"machine": 1}

    fixed = benchmark.rescore_report(stored)
    assert fixed["rows"][0]["observed_class"] == "screener"
    assert fixed["summary"]["confusion_matrix"]["screener"] == {"screener": 1}
    # A navigated screener is no longer counted as a live callee read as a machine.
    assert fixed["summary"]["false_machine_rate"] == 0.0
    # The original is not mutated.
    assert stored["rows"][0]["observed_class"] == "machine"


def test_confusion_matrix_renders_a_screener_column() -> None:
    """Dropping the column silently reassigns navigated screeners elsewhere."""
    rows = [
        _row("gate", "screener", "screener", True),
        _row("vm", "machine", "machine", True),
    ]
    report = {
        "run_id": "r",
        "suite": "unit",
        "sim_numbers": ["+15550100000"],
        "rows": rows,
        "summary": benchmark.summarize(rows),
    }
    text = benchmark.render_markdown(report)
    header = next(line for line in text.splitlines() if "expected \\ observed" in line)
    for cls in ("machine", "screener", "human", "unknown"):
        assert cls in header
    assert "| screener | 0 | 1 | 0 | 0 |" in text


@pytest.mark.parametrize(
    "overrides",
    [
        {"repeat": 0},
        {"repeat": -1},
        {"repeat": True},
        {"concurrency": 0},
        {"pace": -1},
        {"pace": float("nan")},
        {"number": ["+15550100000", "+15550100000"]},
        {"scenario": ["mailbox_full", "mailbox_full"]},
    ],
)
def test_plan_rejects_unsafe_or_invalid_inputs(overrides):
    with pytest.raises(SystemExit):
        benchmark.resolve_plan(_args(**overrides), benchmark.load_scenarios())


@pytest.mark.parametrize(
    "content",
    ["[]", "version: 2", "repeat: 2.5", "numbers: '+15550000000'", "call_timeout_seconds: 0"],
)
def test_suite_schema_errors_fail_before_any_call(tmp_path, content):
    suite = tmp_path / "suite.yaml"
    suite.write_text(content)
    with pytest.raises(SystemExit):
        benchmark.resolve_plan(_args(suite=str(suite)), benchmark.load_scenarios())


def test_missing_agent_record_fails_even_negative_only_expectations(monkeypatch):
    monkeypatch.setattr(benchmark, "queue_scenario", lambda *_: None)
    monkeypatch.setattr(benchmark.agent_api, "trigger_call", lambda *args, **kwargs: ("call-1", {}))
    monkeypatch.setattr(benchmark.agent_api, "wait_for_terminal", lambda *args, **kwargs: None)
    config = AgentApiConfig(
        "https://agent.test/calls", {}, record_url="https://agent.test/calls/{call_id}"
    )
    row = benchmark.run_once(
        config,
        scenario_name="human",
        scenario={
            "expect": {
                "ended_reason_not": ["call.ending.voicemail"],
                "detection_layer_absent": True,
            }
        },
        number="+15550100000",
        attempt=1,
        call_timeout=5,
        grade_audio=False,
        numbers={},
    )
    assert row["passed"] is False
    assert row["agent_terminal"] is False
    assert "terminal status" in row["error"]


def test_mismatched_simulator_scenario_cannot_pass(monkeypatch):
    monkeypatch.setattr(benchmark, "queue_scenario", lambda *_: None)
    monkeypatch.setattr(benchmark.agent_api, "trigger_call", lambda *args, **kwargs: ("call-1", {}))
    monkeypatch.setattr(
        benchmark.agent_api, "wait_for_terminal", lambda *args, **kwargs: {"status": "completed"}
    )
    monkeypatch.setattr(
        benchmark,
        "find_sim_call",
        lambda *args, **kwargs: {"id": "CA1", "scenario": "different", "analysis": {"checks": []}},
    )
    config = AgentApiConfig(
        "https://agent.test/calls", {}, record_url="https://agent.test/calls/{call_id}"
    )
    row = benchmark.run_once(
        config,
        scenario_name="wanted",
        scenario={},
        number="+15550100000",
        attempt=1,
        call_timeout=5,
        grade_audio=True,
        numbers={},
    )
    assert row["passed"] is False
    assert any(c["check"] == "sim_scenario_matches" and c["passed"] is False for c in row["checks"])


def test_statistics_expose_unknowns_and_use_consistent_denominators():
    rows = [
        _row("a", "machine", "screener", False),
        _row("a", "machine", "unknown", None),
        _row("a", "machine", "machine", True),
    ]
    result = benchmark.summarize(rows)
    assert result["pass_rate"] == result["per_scenario"]["a"]["pass_rate"] == 0.5
    assert result["false_human_rate"] == 0.5
    assert result["classification_coverage"] == 0.667
    assert benchmark._delay_stats(list(range(1, 11)))["p90"] == 9


@pytest.mark.parametrize("status", ["failed", "no-answer", "busy", "canceled"])
def test_unconnected_calls_are_never_classified_as_human(status):
    assert (
        benchmark.observed_class(
            {"status": status, "ended_reason": "call.error.connection", "detection_layer": ""}
        )
        == "unknown"
    )


def test_unsettled_call_stops_its_lane_and_persists_partial_report(monkeypatch, tmp_path):
    monkeypatch.setattr(benchmark, "number_index", lambda: {"+15550100000": "number_abc"})
    monkeypatch.setattr(
        benchmark,
        "run_once",
        lambda *args, **kwargs: {
            **_row("mailbox_full", "machine", "unknown", False),
            "agent_terminal": False,
        },
    )
    plan = benchmark.resolve_plan(
        _args(scenario=["mailbox_full"], number=["+15550100000"], repeat=3, pace=0),
        benchmark.load_scenarios(),
    )
    report = benchmark.run_suite(
        _api_config(), plan, benchmark.load_scenarios(), out_dir=tmp_path, grade_audio=False
    )
    assert report["complete"] is False
    assert report["planned_calls"] == 3
    assert report["summary"]["calls"] == 1
    assert json.loads((tmp_path / "report.json").read_text())["complete"] is False
    assert not list(tmp_path.glob("*.tmp"))


def test_required_simulator_check_unavailable_stays_indeterminate(monkeypatch):
    monkeypatch.setattr(benchmark, "queue_scenario", lambda *_: None)
    monkeypatch.setattr(benchmark.agent_api, "trigger_call", lambda *args, **kwargs: ("call-1", {}))
    monkeypatch.setattr(
        benchmark.agent_api, "wait_for_terminal", lambda *args, **kwargs: {"status": "completed"}
    )
    monkeypatch.setattr(
        benchmark,
        "find_sim_call",
        lambda *args, **kwargs: {
            "id": "CA1",
            "scenario": "wanted",
            "analysis": {
                "passed": None,
                "checks": [
                    {
                        "check": "message_content",
                        "passed": None,
                        "detail": "transcription unavailable",
                    }
                ],
            },
        },
    )
    config = AgentApiConfig(
        "https://agent.test/calls", {}, record_url="https://agent.test/calls/{call_id}"
    )
    row = benchmark.run_once(
        config,
        scenario_name="wanted",
        scenario={},
        number="+15550100000",
        attempt=1,
        call_timeout=5,
        grade_audio=True,
        numbers={},
    )
    assert row["passed"] is None


def test_dry_run_needs_no_number_or_agent_credentials(monkeypatch, capsys):
    monkeypatch.delenv("SIM_NUMBER", raising=False)
    monkeypatch.delenv("AGENT_API_TRIGGER_URL", raising=False)
    monkeypatch.setattr(
        benchmark.sys, "argv", ["benchmark", "--scenario", "mailbox_full", "--dry-run"]
    )
    assert benchmark.main() == 0
    assert "calls=1" in capsys.readouterr().out


@pytest.mark.parametrize("dry_run", [True, False])
def test_shell_wrapper_never_calls_on_dry_run_or_after_broken_preflight(tmp_path, dry_run):
    from telephony_voice_simulator.paths import BACKEND_ROOT

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    wrapper = scripts / "run_benchmark.sh"
    wrapper.write_text((BACKEND_ROOT / "scripts" / "run_benchmark.sh").read_text())
    profile = tmp_path / "profile.env"
    profile.write_text("")
    suite = tmp_path / "suite.yaml"
    suite.write_text("version: 1\n")
    fake_python = tmp_path / "python"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_INVOCATIONS"\ncase "$*" in\n  *--preflight-check*) exit 1 ;;\nesac\n'
    )
    fake_python.chmod(0o755)
    log = tmp_path / "invocations"
    result = subprocess.run(
        ["bash", str(wrapper), str(profile), str(suite), *(["--dry-run"] if dry_run else [])],
        env={**os.environ, "PYTHON": str(fake_python), "TEST_INVOCATIONS": str(log)},
        text=True,
        capture_output=True,
    )
    calls = log.read_text().splitlines()
    if dry_run:
        assert result.returncode == 0, result.stderr
        assert len(calls) == 1 and "--dry-run" in calls[0]
        assert not (tmp_path / "results").exists()
    else:
        assert result.returncode == 1, result.stderr
        assert len(calls) == 3
        assert "--preflight-check" in calls[-1]
        assert len(list((tmp_path / "results" / "benchmark").glob("preflight.*"))) == 1
