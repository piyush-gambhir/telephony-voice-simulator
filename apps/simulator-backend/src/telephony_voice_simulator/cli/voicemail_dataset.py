"""Voicemail dataset: inspect, validate, compile, render, and evaluate offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..datasets.voicemail import compile_suite, load_catalog, render_assets, stats
from ..datasets.voicemail.catalog import CatalogError
from ..datasets.voicemail.export import benchmark_rows, export_rows
from ..datasets.voicemail.scoring import baseline_predictions, format_report, read_jsonl, score
from .common import action, emit, group


def _stats(_args: argparse.Namespace) -> int:
    return emit(stats(load_catalog()))


def _validate(_args: argparse.Namespace) -> int:
    try:
        catalog = load_catalog()
    except CatalogError as exc:
        emit({"valid": False, "errors": str(exc).splitlines()})
        return 1
    return emit(
        {"valid": True, "categories": len(catalog.categories), "entries": len(catalog.entries)}
    )


def _compile(args: argparse.Namespace) -> int:
    return emit(compile_suite(args.out, only=args.only))


def _render(args: argparse.Namespace) -> int:
    return emit(
        render_assets(
            args.out, log=lambda line: print(line, file=sys.stderr), force=args.force
        )
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _export(args: argparse.Namespace) -> int:
    rows = export_rows(load_catalog(), variants=args.variants)
    if args.format == "benchmark":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(benchmark_rows(rows), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    else:
        _write_jsonl(args.out, rows)
    return emit({"out": str(args.out), "format": args.format, "rows": len(rows)})


def _score(args: argparse.Namespace) -> int:
    try:
        report = score(read_jsonl(args.cases), read_jsonl(args.predictions))
    except (OSError, ValueError) as exc:
        print(f"score: {exc}", file=sys.stderr)
        return 1
    if args.json:
        return emit(report)
    print(format_report(report))
    return 0


def _baseline(args: argparse.Namespace) -> int:
    rows = baseline_predictions(load_catalog())
    _write_jsonl(args.out, rows)
    return emit({"out": str(args.out), "predictions": len(rows)})


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "voicemail-dataset", "versioned voicemail dataset and suite compiler")
    action(actions, "stats", "count entries by category, class, difficulty, language", _stats)
    action(actions, "validate", "validate the catalog and its clean-room lint", _validate)
    compiled = action(
        actions, "compile", "write a scenario suite, corpus_text.json and labels.jsonl", _compile
    )
    compiled.add_argument("--out", type=Path, required=True)
    compiled.add_argument("--only", nargs="+", metavar="ID", help="compile only these entry ids")
    rendered = action(
        actions, "render", "render corpus_text.json to audio through TTS (paid APIs)", _render
    )
    rendered.add_argument("--out", type=Path, required=True)
    rendered.add_argument("--force", action="store_true", help="rebuild even if present")
    exported = action(actions, "export", "write labelled text cases for offline AMD eval", _export)
    exported.add_argument("--out", type=Path, required=True)
    exported.add_argument("--format", choices=("jsonl", "benchmark"), default="jsonl")
    exported.add_argument(
        "--variants", action="store_true", help="add STT-style text variants of each entry"
    )
    scored = action(actions, "score", "score offline predictions against exported cases", _score)
    scored.add_argument("--cases", type=Path, required=True, help="JSONL from export")
    scored.add_argument(
        "--predictions", type=Path, required=True, help="JSONL rows {id, predicted, decided_at_s?}"
    )
    scored.add_argument("--json", action="store_true", help="print the full report as JSON")
    baseline = action(
        actions, "baseline", "predict from opening_text with the silence regex rules", _baseline
    )
    baseline.add_argument("--out", type=Path, required=True)
