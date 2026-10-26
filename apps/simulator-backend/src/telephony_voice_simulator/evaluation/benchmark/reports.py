"""Report files: markdown rendering, atomic writes, comparison and the report directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...paths import RESULTS_DIR

BENCHMARK_RESULTS_DIR = RESULTS_DIR / "benchmark"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# AMD benchmark: {report['suite']}",
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
    # must carry a screener COLUMN too: dropping it silently reassigns
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


def write_report(out_dir: Path, report: dict[str, Any]) -> None:
    for name, content in (
        ("report.json", json.dumps(report, indent=2, default=str)),
        ("report.md", render_markdown(report)),
    ):
        temporary = out_dir / f".{name}.tmp"
        temporary.write_text(content)
        temporary.replace(out_dir / name)
