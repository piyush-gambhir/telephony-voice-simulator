"""Offline coverage for the generic multi-step UAT workflow."""

from __future__ import annotations

from typing import Any


from telephony_voice_simulator.evaluation import uat


class SequenceAdapter:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.calls = 0

    def preview(self, _context: dict[str, str]) -> str:
        return "offline adapter"

    def execute(self, _context: dict[str, str]) -> Any:
        result = self.results[self.calls]
        self.calls += 1
        return result


def result(ok: bool, status: str, stdout: str = "") -> Any:
    return uat.StepResult(
        adapter="offline",
        ok=ok,
        status=status,
        duration_s=0.01,
        detail="rendered command with customer identifier",
        stdout=stdout,
        stderr="sensitive diagnostic",
    )


def test_retry_then_capture_json_for_later_steps() -> None:
    adapter = SequenceAdapter(
        [
            result(False, "pending"),
            result(True, "completed", '{"data":{"id":"remote-123"}}'),
        ]
    )
    step = uat.WorkflowStep(
        name="poll",
        adapter=adapter,
        attempts=2,
        capture_json={"remote_run_id": "data.id"},
    )
    context = {"scenario": "example", "run_id": "local-123"}

    final, attempts = uat._execute_step(step, context)

    assert final.ok
    assert attempts == 2
    assert adapter.calls == 2
    assert context["remote_run_id"] == "remote-123"


def test_failure_skips_success_steps_but_runs_redacted_cleanup() -> None:
    failing = SequenceAdapter([result(False, "exit_1", "customer payload")])
    skipped = SequenceAdapter([result(True, "completed")])
    cleanup = SequenceAdapter([result(True, "completed", "artifact metadata")])
    workflow = [
        uat.WorkflowStep(name="trigger", adapter=failing),
        uat.WorkflowStep(name="grade", adapter=skipped),
        uat.WorkflowStep(name="collect_artifacts", adapter=cleanup, run_if="always"),
    ]

    passed, reports = uat._run_workflow(
        workflow, {"scenario": "example", "run_id": "local-123"}
    )

    assert not passed
    assert failing.calls == 1
    assert skipped.calls == 0
    assert cleanup.calls == 1
    assert reports[1]["skipped"] == "prior_step_failed"
    assert reports[2]["result"]["output_redacted"] is True
    assert reports[2]["result"]["detail"] == ""
    assert reports[2]["result"]["stdout"] == ""
    assert reports[2]["result"]["stderr"] == ""


def test_sanitized_output_can_be_explicitly_retained() -> None:
    adapter = SequenceAdapter([result(True, "completed", '{"score":1}')])
    workflow = [
        uat.WorkflowStep(
            name="grade",
            adapter=adapter,
            report_output=True,
        )
    ]

    passed, reports = uat._run_workflow(
        workflow, {"scenario": "example", "run_id": "local-123"}
    )

    assert passed
    assert reports[0]["result"]["output_redacted"] is False
    assert reports[0]["result"]["stdout"] == '{"score":1}'


def test_a_missing_executable_fails_the_step_and_still_runs_cleanup(tmp_path) -> None:
    missing = uat.CommandAdapter({"argv": ["definitely-not-a-real-command-tvs"]}, tmp_path)
    cleanup = SequenceAdapter([result(True, "completed")])
    passed, reports = uat._run_workflow(
        [uat.WorkflowStep(name="trigger", adapter=missing),
         uat.WorkflowStep(name="cleanup", adapter=cleanup, run_if="always")],
        {"scenario": "example", "run_id": "local-123"},
    )
    assert not passed
    assert reports[0]["result"]["status"] == "start_failed"
    assert cleanup.calls == 1


def test_capture_reads_json_longer_than_the_report_limit() -> None:
    body = '{"id":"remote-9","transcript":"' + "x" * 20_000 + '"}'
    adapter = SequenceAdapter([result(True, "completed", body)])
    step = uat.WorkflowStep(name="poll", adapter=adapter, capture_json={"remote": "id"},
                            report_output=True)
    context = {"scenario": "example", "run_id": "local-123"}
    passed, reports = uat._run_workflow([step], context)
    assert passed and context["remote"] == "remote-9"
    assert len(reports[0]["result"]["stdout"]) < 13_000


def test_report_detail_is_bounded_too() -> None:
    long = uat.StepResult("offline", True, "completed", 0.01, detail="u" * 20_000)
    reported = uat._report_result(
        uat.WorkflowStep(name="x", adapter=SequenceAdapter([]), report_output=True), long)
    assert len(reported["detail"]) < 13_000
