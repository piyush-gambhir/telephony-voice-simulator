from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.datasets.voicemail import (
    compile_suite,
    lint_text,
    load_catalog,
    render_assets,
    stats,
    validate_catalog,
)
from telephony_voice_simulator.datasets.voicemail.catalog import CatalogError, OUTCOMES_BY_CLASS
from telephony_voice_simulator.domains.amd.validation import validate_scenario

CATALOG = load_catalog()


def _raw(**entry) -> dict:
    base = {
        "id": "demo_01", "category": "demo", "prevalence": "common", "difficulty": "easy",
        "language": "en",
        "segments": [
            {"say": {"text": "Leave a message.", "voice": "female"}},
            {"tone": {"freq": 1000, "duration": 0.5}},
            {"hold": 30},
        ],
    }
    return {
        "version": 1,
        "categories": [{
            "id": "demo", "label": "Demo", "description": "Demo category.",
            "expected_class": "machine-vm", "expected_outcome": "leave_message_after_beep",
        }],
        "entries": [{**base, **entry}],
    }


def test_catalog_loads_with_unique_ids_and_full_coverage() -> None:
    ids = [entry.id for entry in CATALOG.entries]
    assert len(ids) == len(set(ids))
    assert {entry.category for entry in CATALOG.entries} == {c.id for c in CATALOG.categories}
    assert {c.expected_class for c in CATALOG.categories} == set(OUTCOMES_BY_CLASS)
    assert {c.expected_outcome for c in CATALOG.categories} == {
        outcome for outcomes in OUTCOMES_BY_CLASS.values() for outcome in outcomes
    }
    assert stats(CATALOG)["entries"] == len(ids)


@pytest.mark.parametrize(
    "entry,message",
    [
        ({"difficulty": "hard"}, "why_hard"),
        ({"category": "missing"}, "unknown category"),
        ({"prevalence": "often"}, "prevalence"),
        ({"segments": [{"say": {"text": " ", "voice": "female"}}]}, "nonempty text"),
        ({"segments": [{"say": {"text": "Hi", "voice": "robot"}}]}, "voice"),
        ({"segments": [{"pause": -1}]}, "pause"),
        ({"segments": [{"wait": 1}]}, "exactly one"),
        ({"segments": [{"hangup": True}, {"hold": 5}]}, "hangup must be the last"),
        ({"segments": [{"say": {"text": "Hi", "voice": "male"}}, {"hold": 30}]}, "needs a tone"),
    ],
)
def test_validation_rejects_bad_entries(entry: dict, message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        validate_catalog(_raw(**entry))


def test_validation_rejects_duplicate_ids_and_bad_outcome() -> None:
    raw = _raw()
    raw["entries"].append(dict(raw["entries"][0]))
    with pytest.raises(CatalogError, match="duplicate entry id"):
        validate_catalog(raw)
    raw = _raw()
    raw["categories"][0]["expected_outcome"] = "talk_to_human"
    with pytest.raises(CatalogError, match="expected_outcome"):
        validate_catalog(raw)


@pytest.mark.parametrize(
    "text,problem",
    [
        ("Call 212-555-0242 now", "phone number"),
        ("Call (415) 867-5309 now", "phone number"),
        ("Call 4155551234", "phone number"),
        ("Write to jane@example.com", "email"),
        ("Visit https://example.org/help", "URL"),
        ("Visit www.example.org", "URL"),
        ("Back soon \u2014 leave a message", "em dash"),
    ],
)
def test_lint_rejects_real_looking_contact_details(text: str, problem: str) -> None:
    assert any(problem in item for item in lint_text(text))


@pytest.mark.parametrize(
    "text",
    [
        "Call 555-0142 now",
        "Call 212-555-0142 now",
        "Call 555 0199 now",
        "Call 1-555-555-0100 please",
        "Call five five five, zero one two three",
        "We are open 8 to 5, Monday through Friday",
    ],
)
def test_lint_allows_fictional_numbers_and_ordinary_digits(text: str) -> None:
    assert lint_text(text) == []


def test_lint_runs_on_catalog_text() -> None:
    with pytest.raises(CatalogError, match="phone number"):
        validate_catalog(_raw(segments=[
            {"say": {"text": "Call 415-867-5309", "voice": "male"}},
            {"tone": {"freq": 1000, "duration": 0.5}},
            {"hold": 30},
        ]))


def test_compile_writes_suite_that_passes_scenario_validation(tmp_path: Path) -> None:
    result = compile_suite(tmp_path)
    assert result["scenarios"] == len(CATALOG.entries)
    files = sorted((tmp_path / "scenarios").glob("*.yaml"))
    assert len(files) == len(CATALOG.entries)
    corpus = json.loads((tmp_path / "corpus_text.json").read_text())
    played = set()
    for path in files:
        scenario = validate_scenario(yaml.safe_load(path.read_text()), source=path.name)
        assert scenario["name"].startswith("vmds_")
        assert scenario["machine"]["main"][0] == {"wait": 0.8}
        for steps in (v for k, v in scenario["machine"].items() if isinstance(v, list)):
            played |= {s["play"] for s in steps if "play" in s}
    assert played == set(corpus)
    assert all(key.startswith("vmds/") and len(value) == 2 for key, value in corpus.items())


def test_compile_only_selects_entries_and_rejects_unknown_ids(tmp_path: Path) -> None:
    compile_suite(tmp_path, only=["carrier_default_01"])
    assert [p.name for p in (tmp_path / "scenarios").iterdir()] == ["001_carrier_default_01.yaml"]
    with pytest.raises(ValueError, match="unknown entry id"):
        compile_suite(tmp_path, only=["nope"])


def _first_scenario(tmp_path: Path, category: str) -> dict:
    entry = next(e for e in CATALOG.entries if e.category == category)
    compile_suite(tmp_path, only=[entry.id])
    (path,) = (tmp_path / "scenarios").glob("*.yaml")
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize(
    "category,expect",
    [
        ("carrier_default", {
            "callee_class": "machine",
            "ended_reason": "call.ending.voicemail-left-message",
            "message_start_after": {"mark": "tone_end", "min": 0.1, "max": 3.0},
        }),
        ("no_beep_after_greeting", {
            "ended_reason": "call.ending.voicemail-left-message",
            "message_start_after": {"mark": "play_end", "min": 0.5, "max": 12.0},
        }),
        ("mailbox_full", {
            "ended_reason": "call.ending.voicemail-inbox-full",
            "ended_reason_not": ["call.ending.voicemail-left-message"],
        }),
        ("number_disconnected_or_unreachable", {
            "callee_class": "machine",
            "ended_reason_not": ["call.ending.voicemail-left-message"],
        }),
        ("business_ivr_menu", {"ended_reason": "call.ending.machine-ivr"}),
        ("call_screener_ai", {"callee_class": "screener", "agent_spoke": True}),
        ("dtmf_gate_press_key", {"dtmf_received": {"digit": "5"}, "agent_spoke": True}),
        ("human_short_hello", {"callee_class": "human", "agent_spoke": True}),
    ],
)
def test_outcome_maps_to_expectations(tmp_path: Path, category: str, expect: dict) -> None:
    actual = _first_scenario(tmp_path, category)["expect"]
    assert expect.items() <= actual.items()
    assert actual["webhook_received"] is True
    if category == "human_short_hello":
        assert "call.ending.voicemail-left-message" in actual["ended_reason_not"]
        assert actual["agent_speech_after"]["mark"].startswith("play_end:vmds/")


def test_dtmf_gate_compiles_to_a_branch(tmp_path: Path) -> None:
    machine = _first_scenario(tmp_path, "dtmf_gate_press_key")["machine"]
    assert machine["main"][-1] == {"repeat_from": 1}
    assert machine["on_dtmf"] == {"digits": ["5"], "switch": "connected"}
    assert any("play" in step for step in machine["connected"])


def test_render_reuses_corpus_builder_without_calling_tts(tmp_path: Path, monkeypatch) -> None:
    from telephony_voice_simulator.corpus import build

    compile_suite(tmp_path, only=["carrier_default_01"])

    def fake_tts(_text: str, _voice: str, output: Path) -> dict[str, str]:
        output.write_bytes(b"wav")
        return {"generator_engine": "t", "generator_model": "t", "generator_voice": "t"}

    monkeypatch.setattr(build, "_tts_elevenlabs", fake_tts)
    result = render_assets(tmp_path)
    assert result["built"] == ["vmds/carrier_default_01_1.wav"]
    assert (tmp_path / "assets" / "vmds" / "carrier_default_01_1.wav").read_bytes() == b"wav"
    row = json.loads((tmp_path / "assets_manifest.json").read_text())["vmds/carrier_default_01_1.wav"]
    assert row["commit_allowed"] is False
    assert row["source_type"] == "generated"


def test_render_requires_a_compiled_suite(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_assets(tmp_path)


def test_cli_stats_validate_and_compile(tmp_path: Path, capsys) -> None:
    assert main(["voicemail-dataset", "validate"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["voicemail-dataset", "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == len(CATALOG.entries)
    assert main(["voicemail-dataset", "compile", "--out", str(tmp_path), "--only", "mailbox_full_01"]) == 0
    assert (tmp_path / "corpus_text.json").is_file()
    assert main(["scenarios", "validate", "--all", "--scenarios-dir", str(tmp_path / "scenarios")]) == 0
