"""Offline export of the voicemail dataset: one labelled text case per entry."""

from __future__ import annotations

import re
from typing import Any

from .catalog import Catalog
from .compiler import entry_labels

EXPORT_FIELDS = (
    "id",
    "category",
    "expected_class",
    "callee_class",
    "gold_amd",
    "outcome",
    "language",
    "difficulty",
    "prevalence",
    "split",
    "failure_modes",
    "cues",
    "opening_text",
    "full_text",
)
CLIP_WORDS = 3
_DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']|_", " ", text.lower()).split())


def _spelled(text: str) -> str:
    return " ".join(re.sub(r"\d", lambda m: f" {_DIGIT_WORDS[int(m.group())]} ", text).split())


def _clipped(text: str) -> str:
    return " ".join(text.split()[CLIP_WORDS:])


def _variants(row: dict[str, Any]) -> list[dict[str, Any]]:
    """STT-style copies; a variant identical to an earlier one is dropped."""
    opening, full = _plain(row["opening_text"]), _plain(row["full_text"])
    candidates = [("stt_plain", opening, full)]
    opening, full = _spelled(opening), _spelled(full)
    candidates.append(("stt_digits", opening, full))
    if len(opening.split()) > CLIP_WORDS:
        candidates.append(("stt_clipped", _clipped(opening), _clipped(full)))
    seen = {(row["opening_text"], row["full_text"])}
    out: list[dict[str, Any]] = []
    for name, opening, full in candidates:
        if (opening, full) in seen:
            continue
        seen.add((opening, full))
        out.append(
            {
                **row,
                "id": f"{row['id']}__{name}",
                "variant": name,
                "opening_text": opening,
                "full_text": full,
            }
        )
    return out


def export_rows(catalog: Catalog, variants: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in catalog.entries:
        labels = entry_labels(catalog, entry)
        row = {key: labels[key] for key in EXPORT_FIELDS} | {"variant": "original"}
        rows.append(row)
        if variants:
            rows.extend(_variants(row))
    return rows


def benchmark_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The shape an external offline AMD benchmark reads."""
    return [
        {
            "id": row["id"],
            "source": "vmds",
            "gold": row["gold_amd"],
            "transcript": row["opening_text"],
        }
        for row in rows
    ]
