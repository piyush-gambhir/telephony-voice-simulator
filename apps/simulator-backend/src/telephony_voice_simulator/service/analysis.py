"""Transcript analysis: classify silence-timeout callee transcripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..evaluation import silence
from .errors import NotFoundError, ValidationError


def classify_silence_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Bucket `{call_uid, user_text}` rows into machine/system coverage buckets."""
    if not all(isinstance(row, dict) for row in rows):
        raise ValidationError("every row must be a JSON object")
    try:
        return silence.classify(rows)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def classify_silence(path: str | Path) -> dict[str, Any]:
    """Classify a JSONL file of `{call_uid, user_text}` rows.

    Malformed lines are skipped and reported in `malformed`.
    """
    file = Path(path).expanduser()
    try:
        lines = file.read_text().splitlines()
    except FileNotFoundError as exc:
        raise NotFoundError(f"File not found: {path}") from exc
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    malformed: list[str] = []
    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            malformed.append(f"{file}:{number}: malformed JSON: {exc}")
            continue
        if not isinstance(row, dict):
            malformed.append(f"{file}:{number}: expected a JSON object")
            continue
        rows.append(row)
    if not rows:
        raise ValidationError(f"{file}: no valid input rows ({len(malformed)} malformed)")
    return {**silence.classify(rows), "malformed": malformed}
