"""AMD benchmark suite: plan, run, compare, rescore and read finished report_files."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..evaluation.benchmark import reports as report_files
from ..evaluation.benchmark.execution import new_run_id, run_suite
from ..evaluation.benchmark.plan import load_scenarios, resolve_plan
from ..evaluation.benchmark.scoring import expected_class, preflight_health, rescore_report
from ..integrations import agent_api
from .errors import NotFoundError, ValidationError


def _plan(
    *,
    suite: str | None,
    scenarios: list[str] | None,
    repeat: int | None,
    numbers: list[str] | None,
    concurrency: int | None,
    pace: float | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    catalog = load_scenarios()
    try:
        plan = resolve_plan(
            suite=suite,
            scenarios=scenarios,
            repeat=repeat,
            numbers=numbers,
            concurrency=concurrency,
            pace=pace,
            catalog=catalog,
        )
    except OSError as exc:
        raise ValidationError(f"cannot read benchmark suite: {exc}") from exc
    if not plan["sim_numbers"]:
        number = os.environ.get("SIM_NUMBER", "").strip()
        if number:
            plan["sim_numbers"] = [number]
        plan["concurrency"] = 1
    return plan, catalog


def plan(
    *,
    suite: str | None = None,
    scenarios: list[str] | None = None,
    repeat: int | None = None,
    numbers: list[str] | None = None,
    concurrency: int | None = None,
    pace: float | None = None,
) -> dict[str, Any]:
    """The resolved plan (no calls placed): lanes, call count and each scenario's expected class.

    Suite-file values apply unless a parameter overrides them. With no numbers
    anywhere, SIM_NUMBER is used; if that is unset the plan simply has no lines.
    """
    resolved, catalog = _plan(
        suite=suite,
        scenarios=scenarios,
        repeat=repeat,
        numbers=numbers,
        concurrency=concurrency,
        pace=pace,
    )
    return {
        **resolved,
        "calls": len(resolved["scenarios"]) * resolved["repeat"],
        "lanes": resolved["sim_numbers"][: resolved["concurrency"]],
        "expected_classes": {name: expected_class(catalog[name]) for name in resolved["scenarios"]},
    }


def run(
    *,
    suite: str | None = None,
    scenarios: list[str] | None = None,
    repeat: int | None = None,
    numbers: list[str] | None = None,
    concurrency: int | None = None,
    pace: float | None = None,
    audio: bool = True,
    out: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running, places real calls. Runs the plan and writes report.json and report.md.

    Returns the report plus ``report_id``, ``report_dir`` and ``ok`` (complete, with no
    failed or indeterminate call). ``out`` overrides the default
    results/benchmark/<run_id> directory. ``audio=False`` skips simulator-side grading.
    """
    resolved, catalog = _plan(
        suite=suite,
        scenarios=scenarios,
        repeat=repeat,
        numbers=numbers,
        concurrency=concurrency,
        pace=pace,
    )
    if not resolved["sim_numbers"]:
        raise ValidationError("provide numbers, suite numbers, or SIM_NUMBER before placing calls")
    emit = log or (lambda _line: None)
    emit(
        f"suite={resolved['name']} scenarios={len(resolved['scenarios'])} "
        f"repeat={resolved['repeat']} calls={len(resolved['scenarios']) * resolved['repeat']}"
    )
    emit(
        f"lanes={resolved['concurrency']} of {len(resolved['sim_numbers'])} line(s) "
        f"pace={resolved['pace_seconds']}s"
    )
    config = agent_api.load_config()
    run_id = new_run_id()
    out_dir = Path(out).expanduser() if out else report_files.BENCHMARK_RESULTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    report = run_suite(
        config, resolved, catalog, out_dir=out_dir, grade_audio=audio, log=log, run_id=run_id
    )
    emit(f"report -> {out_dir}/report.md")
    summary = report["summary"]
    return {
        **report,
        "report_id": out_dir.name,
        "report_dir": str(out_dir),
        "ok": report["complete"] and summary["failed"] == 0 and summary["indeterminate"] == 0,
    }


def _locate(report: str) -> Path:
    """A report.json path, a report directory, or a report id under the results directory."""
    given = Path(report).expanduser()
    if given.is_file():
        return given
    if given.is_dir() and (given / "report.json").is_file():
        return given / "report.json"
    if report and "/" not in report and "\\" not in report and report not in {".", ".."}:
        candidate = report_files.BENCHMARK_RESULTS_DIR / report / "report.json"
        if candidate.is_file():
            return candidate
    raise NotFoundError(f"Unknown benchmark report: {report}")


def _read(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot read benchmark report {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValidationError(f"benchmark report {path} is not a JSON object")
    return loaded


def compare(*, reports: list[str], out: str | None = None) -> dict[str, Any]:
    """Side-by-side markdown for finished runs.

    Each item is ``LABEL=REPORT`` or just ``REPORT`` (labelled by its directory name);
    REPORT is a report.json path, a report directory or a report id. With ``out``,
    also writes comparison.md into that directory.
    """
    if len(reports) < 2:
        raise ValidationError("compare needs at least two reports")
    labelled: list[tuple[str, dict[str, Any]]] = []
    for item in reports:
        label, separator, ref = item.partition("=")
        if not separator or not ref:
            label, ref = "", item
        path = _locate(ref)
        labelled.append((label or path.parent.name, _read(path)))
    markdown = report_files.render_comparison(labelled)
    written = None
    if out:
        target = Path(out).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        (target / "comparison.md").write_text(markdown)
        written = str(target / "comparison.md")
    return {"labels": [label for label, _ in labelled], "path": written, "markdown": markdown}


def rescore(*, report: str) -> dict[str, Any]:
    """Re-read a finished run under the current scoring rules; places no calls.

    Writes report.rescored.json and report.rescored.md next to the source report.
    """
    source = _locate(report)
    rescored = rescore_report(_read(source))
    markdown = report_files.render_markdown(rescored)
    json_path = source.with_name("report.rescored.json")
    markdown_path = source.with_name("report.rescored.md")
    json_path.write_text(json.dumps(rescored, indent=2, default=str))
    markdown_path.write_text(markdown)
    return {
        "source": str(source),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "summary": rescored["summary"],
        "markdown": markdown,
    }


def preflight(*, report: str) -> dict[str, Any]:
    """Did a finished run's pipeline work end to end, whatever the agent scored?

    ``healthy`` is false for a dead token, an unroutable mapping, an unmanaged line or a
    missing simulator leg; a failing agent assertion alone is still healthy.
    """
    healthy, detail = preflight_health(_read(_locate(report)))
    return {"healthy": healthy, "detail": detail}


def list_reports() -> list[dict[str, Any]]:
    """Finished and partial runs under the benchmark results directory, newest first."""
    root = report_files.BENCHMARK_RESULTS_DIR
    if not root.is_dir():
        return []
    entries = []
    for directory in root.iterdir():
        path = directory / "report.json"
        if not path.is_file():
            continue
        try:
            report = _read(path)
        except ValidationError:
            continue
        summary = report.get("summary") or {}
        entries.append(
            (
                path.stat().st_mtime,
                {
                    "report_id": directory.name,
                    "run_id": report.get("run_id"),
                    "suite": report.get("suite"),
                    "complete": report.get("complete"),
                    "planned_calls": report.get("planned_calls"),
                    "calls": summary.get("calls"),
                    "passed": summary.get("passed"),
                    "failed": summary.get("failed"),
                    "indeterminate": summary.get("indeterminate"),
                    "pass_rate": summary.get("pass_rate"),
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                },
            )
        )
    return [entry for _, entry in sorted(entries, key=lambda item: item[0], reverse=True)]


def get_report(report_id: str, *, markdown: bool = False) -> dict[str, Any]:
    """One report by id (or directory or report.json path). ``markdown=True`` renders it instead."""
    report = _read(_locate(report_id))
    if markdown:
        return {"report_id": report_id, "markdown": report_files.render_markdown(report)}
    return report
