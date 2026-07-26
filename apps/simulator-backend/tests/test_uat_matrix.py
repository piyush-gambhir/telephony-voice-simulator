"""Offline coverage for the generic multi-step UAT workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any


SCRIPT = Path(__file__).parent.parent / "scripts" / "uat_matrix.py"
SPEC = importlib.util.spec_from_file_location("uat_matrix_under_test", SCRIPT)
assert SPEC and SPEC.loader
uat = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = uat
SPEC.loader.exec_module(uat)


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
