"""Voicemail dataset: inspect, validate, compile into a suite, and render audio."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..datasets.voicemail import compile_suite, load_catalog, render_assets, stats
from ..datasets.voicemail.catalog import CatalogError
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


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "voicemail-dataset", "versioned voicemail dataset and suite compiler")
    action(actions, "stats", "count entries by category, class, difficulty, language", _stats)
    action(actions, "validate", "validate the catalog and its clean-room lint", _validate)
    compiled = action(actions, "compile", "write a scenario suite and corpus_text.json", _compile)
    compiled.add_argument("--out", type=Path, required=True)
    compiled.add_argument("--only", nargs="+", metavar="ID", help="compile only these entry ids")
    rendered = action(
        actions, "render", "render corpus_text.json to audio through TTS (paid APIs)", _render
    )
    rendered.add_argument("--out", type=Path, required=True)
    rendered.add_argument("--force", action="store_true", help="rebuild even if present")
