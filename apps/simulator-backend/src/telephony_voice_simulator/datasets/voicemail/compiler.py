"""Compile the voicemail dataset into a scenario suite and optional audio."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import yaml

from ...domains.amd.validation import validate_scenario
from .catalog import Catalog, Category, Entry, load_catalog

INITIAL_WAIT = 0.8
TONE_AMPLITUDE = 12000
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


def _steps(entry: Entry, segments: Iterable[dict[str, Any]], offset: int) -> tuple[
    list[dict[str, Any]], dict[str, tuple[str, str]], list[str]
]:
    steps: list[dict[str, Any]] = []
    corpus: dict[str, tuple[str, str]] = {}
    assets: list[str] = []
    for segment in segments:
        kind, value = next(iter(segment.items()))
        if kind == "say":
            asset = f"vmds/{entry.id}_{offset + len(assets) + 1}.wav"
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
        "callee_class": CALLEE_CLASS[category.expected_class],
        "webhook_received": True,
    }
    outcome = category.expected_outcome
    if outcome == "leave_message_after_beep":
        expect["ended_reason"] = "call.ending.voicemail-left-message"
        expect["message_start_after"] = {"mark": "tone_end", "min": 0.1, "max": 3.0}
    elif outcome == "leave_message_no_beep":
        expect["ended_reason"] = "call.ending.voicemail-left-message"
        expect["message_start_after"] = {"mark": "play_end", "min": 0.5, "max": 12.0}
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
    }
    return validate_scenario(scenario, source=entry.id), corpus


def compile_suite(
    out_dir: Path, only: Iterable[str] | None = None, catalog: Catalog | None = None
) -> dict[str, Any]:
    """Write ``scenarios/<n>_<id>.yaml`` and ``corpus_text.json`` under ``out_dir``."""
    catalog = catalog or load_catalog()
    wanted = set(only or ())
    unknown = wanted - {e.id for e in catalog.entries}
    if unknown:
        raise ValueError(f"unknown entry id(s): {', '.join(sorted(unknown))}")
    scenarios_dir = Path(out_dir) / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    corpus: dict[str, list[str]] = {}
    written: list[str] = []
    for number, entry in enumerate(catalog.entries, start=1):
        if wanted and entry.id not in wanted:
            continue
        scenario, entry_corpus = compile_entry(catalog, entry)
        path = scenarios_dir / f"{number:03d}_{entry.id}.yaml"
        path.write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
        corpus.update({asset: list(pair) for asset, pair in entry_corpus.items()})
        written.append(path.name)
    (Path(out_dir) / "corpus_text.json").write_text(
        json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
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
