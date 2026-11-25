"""Load and validate the voicemail dataset catalog.

The dataset is public, so every spoken text passes a clean-room lint: no real
contact details and no copied vendor prompts.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
SUPPORTED_VERSION = 1

OUTCOMES_BY_CLASS: dict[str, tuple[str, ...]] = {
    "machine-vm": ("leave_message_after_beep", "leave_message_no_beep"),
    "machine-unavailable": ("hang_up_inbox_full", "hang_up_unreachable"),
    "machine-ivr": ("hang_up_ivr",),
    "screener": ("stay_through_screen", "press_key_then_continue"),
    "human": ("talk_to_human",),
}
PREVALENCE = ("common", "occasional", "rare")
DIFFICULTY = ("easy", "medium", "hard")
VOICES = ("female", "male", "neutral")
SEGMENT_KEYS = ("say", "pause", "tone", "hold", "hangup")
DTMF_KEYS = frozenset("0123456789*#")

_ID = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\Z")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(
    r"https?://|www\.|\b[\w-]+\.(?:com|net|org|io|ai|co|us|edu|gov|app|dev)\b", re.IGNORECASE
)
_NUMBER = re.compile(r"\+?\d(?:[\d\s().\-]*\d)?")


class CatalogError(ValueError):
    """Raised with every problem found, one per line."""


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str
    expected_class: str
    expected_outcome: str


@dataclass(frozen=True)
class Entry:
    id: str
    category: str
    prevalence: str
    difficulty: str
    language: str
    why_hard: str
    segments: tuple[dict[str, Any], ...]
    gate_digits: tuple[str, ...] = ()
    after_gate: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Catalog:
    version: int
    categories: tuple[Category, ...]
    entries: tuple[Entry, ...]
    _by_category: dict[str, Category] = field(default_factory=dict, repr=False, compare=False)

    def category(self, entry: Entry) -> Category:
        return self._by_category[entry.category]


def lint_text(text: str) -> list[str]:
    """Return clean-room violations: real contact details and em dashes."""
    problems: list[str] = []
    if "\u2014" in text:
        problems.append("em dash")
    if _EMAIL.search(text):
        problems.append("email address")
    if _URL.search(text):
        problems.append("URL")
    for match in _NUMBER.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if len(digits) < 7:
            continue
        if len(digits) == 11 and digits[0] == "1":
            digits = digits[1:]
        local = digits[-7:] if len(digits) in (7, 10) else ""
        if not (local and local[:3] == "555" and local[3:5] == "01"):
            problems.append(f"phone number outside 555-0100..0199: {match.group().strip()!r}")
    return problems


def _number(value: Any, low: float, high: float, *, exclusive_low: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return (value > low if exclusive_low else value >= low) and value <= high


def _check_segments(segments: Any, where: str, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(segments, list) or not segments:
        errors.append(f"{where}: expected a nonempty list of segments")
        return []
    valid: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        spot = f"{where}[{index}]"
        if not isinstance(segment, dict) or len(segment) != 1 or next(iter(segment)) not in SEGMENT_KEYS:
            errors.append(f"{spot}: expected exactly one of {', '.join(SEGMENT_KEYS)}")
            continue
        kind, value = next(iter(segment.items()))
        if kind == "say":
            ok = isinstance(value, dict) and set(value) == {"text", "voice"}
            if not ok:
                errors.append(f"{spot}.say: expected text and voice")
                continue
            if not isinstance(value["text"], str) or not value["text"].strip():
                errors.append(f"{spot}.say.text: expected nonempty text")
                continue
            if value["voice"] not in VOICES:
                errors.append(f"{spot}.say.voice: expected one of {', '.join(VOICES)}")
            errors.extend(f"{spot}.say.text: {problem}" for problem in lint_text(value["text"]))
        elif kind == "pause":
            if not _number(value, 0, 60):
                errors.append(f"{spot}.pause: expected seconds from 0 to 60")
        elif kind == "hold":
            if not _number(value, 0, 120, exclusive_low=True):
                errors.append(f"{spot}.hold: expected seconds above 0 and at most 120")
        elif kind == "tone":
            options = value if isinstance(value, dict) else {}
            if not options or set(options) - {"freq", "duration", "amplitude"} or not {"freq", "duration"} <= set(options):
                errors.append(f"{spot}.tone: expected freq and duration, optionally amplitude")
                continue
            if not _number(options["freq"], 0, 24000, exclusive_low=True) or options["freq"] >= 24000:
                errors.append(f"{spot}.tone.freq: expected Hz above 0 and below 24000")
            if not _number(options["duration"], 0, 10, exclusive_low=True):
                errors.append(f"{spot}.tone.duration: expected seconds above 0 and at most 10")
            if "amplitude" in options and not _number(options["amplitude"], 0, 32767, exclusive_low=True):
                errors.append(f"{spot}.tone.amplitude: expected 1 to 32767")
        elif value is not True:
            errors.append(f"{spot}.hangup: expected true")
        valid.append(segment)
    if any("hangup" in s for s in valid[:-1]):
        errors.append(f"{where}: hangup must be the last segment")
    return valid


def _kinds(segments: Any) -> list[str]:
    return [next(iter(s)) for s in segments if isinstance(s, dict) and s]


def _check_outcome_shape(entry: dict[str, Any], outcome: str, errors: list[str]) -> None:
    name = entry["id"]
    kinds = _kinds(entry["segments"])
    if outcome == "leave_message_after_beep" and not ("tone" in kinds and "hold" in kinds):
        errors.append(f"{name}: leave_message_after_beep needs a tone and a hold")
    if outcome == "leave_message_no_beep" and ("tone" in kinds or "hold" not in kinds or "say" not in kinds):
        errors.append(f"{name}: leave_message_no_beep needs speech and a hold, and no tone")
    if outcome in {"hang_up_inbox_full", "hang_up_unreachable"} and kinds[-1:] != ["hangup"]:
        errors.append(f"{name}: {outcome} must end with hangup")
    if outcome == "hang_up_ivr" and kinds[-1:] not in (["hangup"], ["hold"]):
        errors.append(f"{name}: hang_up_ivr must end with hangup or hold")
    if outcome in {"stay_through_screen", "talk_to_human"}:
        if "say" not in kinds:
            errors.append(f"{name}: {outcome} needs speech")
        if kinds[-1:] != ["hold"]:
            errors.append(f"{name}: {outcome} must end with hold so the caller can respond")
    gated = outcome == "press_key_then_continue"
    if gated != bool(entry.get("gate_digits")) or gated != bool(entry.get("after_gate")):
        errors.append(f"{name}: gate_digits and after_gate belong to press_key_then_continue only")
    if gated:
        if kinds[-1:] != ["pause"]:
            errors.append(f"{name}: a DTMF gate must end with a pause before it repeats")
        digits = entry["gate_digits"]
        if not isinstance(digits, list) or any(str(d) not in DTMF_KEYS for d in digits):
            errors.append(f"{name}.gate_digits: expected DTMF keys")
        after = _kinds(entry["after_gate"])
        if "say" not in after or after[-1:] != ["hold"]:
            errors.append(f"{name}.after_gate: needs speech and a final hold")


def validate_catalog(raw: Any) -> Catalog:
    """Validate a parsed catalog; raises ``CatalogError`` listing every problem."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise CatalogError("catalog: expected a mapping")
    if raw.get("version") != SUPPORTED_VERSION:
        errors.append(f"version: expected {SUPPORTED_VERSION}")
    categories: dict[str, Category] = {}
    for index, item in enumerate(raw.get("categories") or []):
        where = f"categories[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        unknown = set(item) - {"id", "label", "description", "expected_class", "expected_outcome"}
        if unknown:
            errors.append(f"{where}: unknown keys {sorted(map(str, unknown))}")
        cid = item.get("id")
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            errors.append(f"{where}.id: expected snake_case")
            continue
        if cid in categories:
            errors.append(f"{where}: duplicate category id {cid}")
            continue
        for key in ("label", "description"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"{cid}.{key}: expected nonempty text")
            else:
                errors.extend(f"{cid}.{key}: {p}" for p in lint_text(item[key]))
        klass, outcome = item.get("expected_class"), item.get("expected_outcome")
        if klass not in OUTCOMES_BY_CLASS:
            errors.append(f"{cid}.expected_class: expected one of {', '.join(OUTCOMES_BY_CLASS)}")
        elif outcome not in OUTCOMES_BY_CLASS[klass]:
            errors.append(f"{cid}.expected_outcome: {klass} allows {', '.join(OUTCOMES_BY_CLASS[klass])}")
        else:
            categories[cid] = Category(
                cid, str(item["label"]), str(item["description"]), klass, outcome
            )
    entries: list[Entry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("entries") or []):
        where = f"entries[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected a mapping")
            continue
        eid = item.get("id")
        if not isinstance(eid, str) or not _ID.fullmatch(eid):
            errors.append(f"{where}.id: expected snake_case")
            continue
        where = eid
        if eid in seen:
            errors.append(f"{where}: duplicate entry id")
            continue
        seen.add(eid)
        allowed = {
            "id", "category", "prevalence", "difficulty", "language", "why_hard",
            "segments", "gate_digits", "after_gate",
        }
        if set(item) - allowed:
            errors.append(f"{where}: unknown keys {sorted(map(str, set(item) - allowed))}")
        category = categories.get(item.get("category"))
        if category is None:
            errors.append(f"{where}.category: unknown category {item.get('category')!r}")
        if item.get("prevalence") not in PREVALENCE:
            errors.append(f"{where}.prevalence: expected one of {', '.join(PREVALENCE)}")
        if item.get("difficulty") not in DIFFICULTY:
            errors.append(f"{where}.difficulty: expected one of {', '.join(DIFFICULTY)}")
        language = item.get("language")
        if not isinstance(language, str) or not _LANGUAGE.fullmatch(language):
            errors.append(f"{where}.language: expected a BCP-47 tag such as en or es-US")
        why = item.get("why_hard", "")
        if not isinstance(why, str):
            errors.append(f"{where}.why_hard: expected text")
            why = ""
        if item.get("difficulty") in ("medium", "hard") and not why.strip():
            errors.append(f"{where}.why_hard: required when difficulty is medium or hard")
        errors.extend(f"{where}.why_hard: {p}" for p in lint_text(why))
        segments = _check_segments(item.get("segments"), f"{where}.segments", errors)
        after_raw = item.get("after_gate")
        after = _check_segments(after_raw, f"{where}.after_gate", errors) if after_raw else []
        gate = item.get("gate_digits") or []
        if category is not None and segments and len(segments) == len(item["segments"]):
            _check_outcome_shape(
                {**item, "segments": segments, "after_gate": after, "gate_digits": gate},
                category.expected_outcome,
                errors,
            )
        if category is not None and not errors:
            entries.append(
                Entry(
                    eid, category.id, item["prevalence"], item["difficulty"], language, why,
                    tuple(segments), tuple(str(d) for d in gate), tuple(after),
                )
            )
    used = {i.get("category") for i in raw.get("entries") or [] if isinstance(i, dict)}
    for cid in categories:
        if cid not in used:
            errors.append(f"{cid}: category has no entries")
    if errors:
        raise CatalogError("\n".join(errors))
    return Catalog(raw["version"], tuple(categories.values()), tuple(entries), dict(categories))


def load_catalog(path: Path | None = None) -> Catalog:
    source = path or CATALOG_PATH
    return validate_catalog(yaml.safe_load(source.read_text(encoding="utf-8")))


def stats(catalog: Catalog) -> dict[str, Any]:
    by_category = {c.id: 0 for c in catalog.categories}
    classes: Counter[str] = Counter()
    difficulty: Counter[str] = Counter()
    language: Counter[str] = Counter()
    prevalence: Counter[str] = Counter()
    for entry in catalog.entries:
        by_category[entry.category] += 1
        classes[catalog.category(entry).expected_class] += 1
        difficulty[entry.difficulty] += 1
        language[entry.language] += 1
        prevalence[entry.prevalence] += 1
    return {
        "version": catalog.version,
        "entries": len(catalog.entries),
        "categories": by_category,
        "expected_class": dict(classes),
        "difficulty": dict(difficulty),
        "language": dict(language),
        "prevalence": dict(prevalence),
    }
