"""Compile the voicemail dataset into a scenario suite, labels, and optional audio."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import yaml

from ...domains.amd.validation import validate_scenario
from .catalog import Catalog, Category, Entry, is_beep, load_catalog, say_texts

INITIAL_WAIT = 0.8
TONE_AMPLITUDE = 12000
DEV_SHARE = 70
CANDIDATE_ALLOWLIST = "public_audio_allowlist.candidate.json"
NOT_HANDLED = [
    "call.ending.machine-ivr",
    "call.ending.voicemail-left-message",
    "call.ending.voicemail-hangup",
]
UNREACHABLE_ENDINGS = [
    "call.ending.voicemail-inbox-full",
    "call.ending.voicemail-hangup",
    "call.in-progress.customer-ended-call",
    "call.ending.customer-ended-call",
]
CALLEE_CLASS = {
    "machine-vm": "machine",
    "machine-unavailable": "machine",
    "machine-ivr": "machine",
    "screener": "screener",
    "human": "human",
}
GOLD_AMD = {
    "machine-vm": "machine-vm",
    "machine-unavailable": "machine-unavailable",
    "machine-ivr": "machine-ivr",
    "screener": "machine-ivr",
    "human": "human",
}


def asset_name(entry: Entry, number: int) -> str:
    return f"vmds_{entry.id}_{number}.wav"


def callee_class(category: Category) -> str:
    if category.expected_outcome == "leave_message_after_screen":
        return "machine"
    return CALLEE_CLASS[category.expected_class]


def split_for(entry_id: str) -> str:
    bucket = int(hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "dev" if bucket < DEV_SHARE else "test"


def _steps(entry: Entry, segments: Iterable[dict[str, Any]], offset: int) -> tuple[
    list[dict[str, Any]], dict[str, tuple[str, str]], list[str]
]:
    steps: list[dict[str, Any]] = []
    corpus: dict[str, tuple[str, str]] = {}
    assets: list[str] = []
    for segment in segments:
        kind, value = next(iter(segment.items()))
        if kind == "say":
            asset = asset_name(entry, offset + len(assets) + 1)
            corpus[asset] = (value["voice"], value["text"])
            assets.append(asset)
            steps.append({"play": asset})
        elif kind == "pause":
            steps.append({"wait": value})
        elif kind == "tone":
            steps.append(
                {
                    "tone": {
                        "freq": value["freq"],
                        "duration": value["duration"],
                        "amplitude": value.get("amplitude", TONE_AMPLITUDE),
                    }
                }
            )
        elif kind == "hold":
            steps.append({"hold": value})
        else:
            steps.append({"hangup": True})
    return steps, corpus, assets


def expectations(category: Category, assets: list[str], gate_assets: list[str],
                 digit: str | None = None) -> dict[str, Any]:
    """Expect block for a category outcome; assets are the entry's played files in order."""
    expect: dict[str, Any] = {
        "callee_class": callee_class(category),
        "webhook_received": True,
    }
    outcome = category.expected_outcome
    if outcome in ("leave_message_after_beep", "leave_message_after_screen"):
        expect["ended_reason"] = "call.ending.voicemail-left-message"
        expect["message_start_after"] = {"mark": "tone_end", "min": 0.1, "max": 3.0}
    elif outcome == "leave_message_no_beep":
        expect["ended_reason"] = "call.ending.voicemail-left-message"
        expect["message_start_after"] = {"mark": f"play_end:{assets[-1]}", "min": 0.5, "max": 12.0}
        expect["max_overlap_with_playback"] = {"asset": assets[-1], "max_s": 6.0}
    elif outcome == "hang_up_inbox_full":
        expect["ended_reason"] = "call.ending.voicemail-inbox-full"
        expect["ended_reason_not"] = ["call.ending.voicemail-left-message"]
    elif outcome == "hang_up_unreachable":
        expect["ended_reason_any_of"] = UNREACHABLE_ENDINGS
        expect["ended_reason_not"] = ["call.ending.voicemail-left-message"]
    elif outcome == "hang_up_ivr":
        expect["ended_reason"] = "call.ending.machine-ivr"
    elif outcome == "stay_through_screen":
        expect["ended_reason_not"] = NOT_HANDLED
        expect["agent_spoke"] = True
        expect["agent_speech_after"] = {"mark": f"play_end:{assets[-1]}", "min": 0.0, "max": 4.0}
    elif outcome == "press_key_then_continue":
        expect["dtmf_received"] = {"digit": digit}
        expect["ended_reason_not"] = [
            "call.ending.machine-ivr", "call.ending.voicemail-left-message",
        ]
        expect["agent_spoke"] = True
        expect["agent_speech_after"] = {
            "mark": f"play_end:{gate_assets[-1]}", "min": 0.0, "max": 4.0,
        }
    else:
        expect["ended_reason_not"] = NOT_HANDLED
        expect["agent_spoke"] = True
        expect["agent_speech_after"] = {"mark": f"play_end:{assets[-1]}", "min": 0.0, "max": 4.0}
    return expect


def _opening(segments: Iterable[dict[str, Any]]) -> list[str]:
    """Speech before the first beep, hold or hangup; a DTMF gate ends the main segments."""
    texts: list[str] = []
    for segment in segments:
        if is_beep(segment) or "hold" in segment or "hangup" in segment:
            break
        if "say" in segment:
            texts.append(segment["say"]["text"])
    return texts


def _timing(segments: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Structure-only timing of the main segments; speech length needs rendered audio."""
    leading = INITIAL_WAIT
    started = False
    pauses: list[float] = []
    beeps = [s["tone"] for s in segments if is_beep(s)]
    for segment in segments:
        if "pause" in segment:
            if started:
                pauses.append(segment["pause"])
            else:
                leading += segment["pause"]
        elif "say" in segment or "tone" in segment:
            started = True
    return {
        "leading_silence_s": round(leading, 3),
        "pauses_s": pauses,
        "longest_pause_s": max(pauses, default=0.0),
        "beep_count": len(beeps),
        "beep_freqs_hz": [b["freq"] for b in beeps],
        "beep_durations_s": [b["duration"] for b in beeps],
    }


def entry_labels(catalog: Catalog, entry: Entry) -> dict[str, Any]:
    """Labels computed from the catalog alone, written to labels.jsonl and scenario metadata."""
    category = catalog.category(entry)
    opening = " ".join(_opening(entry.segments))
    full = " ".join(say_texts([*entry.segments, *entry.after_gate]))
    return {
        "id": entry.id,
        "category": entry.category,
        "expected_class": category.expected_class,
        "callee_class": callee_class(category),
        "gold_amd": GOLD_AMD[category.expected_class],
        "outcome": category.expected_outcome,
        "language": entry.language,
        "difficulty": entry.difficulty,
        "prevalence": entry.prevalence,
        "split": split_for(entry.id),
        "failure_modes": list(entry.failure_modes),
        "cues": list(entry.cues),
        "provenance": dict(entry.provenance),
        "catalog_version": catalog.version,
        "opening_text": opening,
        "full_text": full,
        "opening_words": len(opening.split()),
        "full_words": len(full.split()),
        "timing": _timing(entry.segments),
    }


def compile_entry(
    catalog: Catalog, entry: Entry
) -> tuple[dict[str, Any], dict[str, tuple[str, str]]]:
    category = catalog.category(entry)
    main, corpus, assets = _steps(entry, entry.segments, 0)
    machine: dict[str, Any] = {"main": [{"wait": INITIAL_WAIT}, *main]}
    gate_assets: list[str] = []
    digit = None
    if category.expected_outcome == "press_key_then_continue":
        after, extra, gate_assets = _steps(entry, entry.after_gate, len(assets))
        corpus.update(extra)
        machine["main"].append({"repeat_from": 1})
        machine["on_dtmf"] = {"digits": list(entry.gate_digits), "switch": "connected"}
        machine["connected"] = [{"wait": 1.0}, *after]
        digit = entry.gate_digits[0]
    scenario = {
        "name": f"vmds_{entry.id}",
        "machine": machine,
        "expect": expectations(category, assets, gate_assets, digit),
        "metadata": entry_labels(catalog, entry),
    }
    return validate_scenario(scenario, source=entry.id), corpus


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compile_suite(
    out_dir: Path, only: Iterable[str] | None = None, catalog: Catalog | None = None
) -> dict[str, Any]:
    """Write scenarios, ``corpus_text.json``, ``labels.jsonl`` and a candidate allowlist."""
    catalog = catalog or load_catalog()
    wanted = set(only or ())
    unknown = wanted - {e.id for e in catalog.entries}
    if unknown:
        raise ValueError(f"unknown entry id(s): {', '.join(sorted(unknown))}")
    out = Path(out_dir)
    scenarios_dir = out / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    corpus: dict[str, list[str]] = {}
    labels: list[dict[str, Any]] = []
    written: list[str] = []
    for number, entry in enumerate(catalog.entries, start=1):
        if wanted and entry.id not in wanted:
            continue
        scenario, entry_corpus = compile_entry(catalog, entry)
        path = scenarios_dir / f"{number:03d}_{entry.id}.yaml"
        path.write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
        corpus.update({asset: list(pair) for asset, pair in entry_corpus.items()})
        labels.append(scenario["metadata"])
        written.append(path.name)
    _write_json(out / "corpus_text.json", corpus)
    (out / "labels.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labels), encoding="utf-8"
    )
    _write_json(out / CANDIDATE_ALLOWLIST, {"version": 1, "assets": sorted(corpus)})
    return {"out": str(out_dir), "scenarios": len(written), "assets": len(corpus), "files": written}


def render_assets(
    out_dir: Path, log: Callable[[str], None] | None = None, force: bool = False
) -> dict[str, Any]:
    """Render ``corpus_text.json`` through the corpus TTS backends (paid APIs)."""
    from ...corpus import build

    source = Path(out_dir) / "corpus_text.json"
    if not source.is_file():
        raise FileNotFoundError(f"{source} not found; run compile first")
    corpus = {asset: (voice, text) for asset, (voice, text) in json.loads(source.read_text()).items()}
    return build.build_assets(
        force=force,
        log=log,
        corpus=corpus,
        assets_dir=Path(out_dir) / "assets",
        manifest_path=Path(out_dir) / "assets_manifest.json",
        source_url="telephony_voice_simulator.datasets.voicemail",
    )
