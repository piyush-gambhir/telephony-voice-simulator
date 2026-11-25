from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.corpus.release_policy import load_public_allowlist
from telephony_voice_simulator.datasets.voicemail import (
    compile_suite,
    lint_text,
    load_catalog,
    render_assets,
    stats,
    validate_catalog,
)
from telephony_voice_simulator.datasets.voicemail.catalog import CatalogError, OUTCOMES_BY_CLASS
from telephony_voice_simulator.datasets.voicemail.compiler import entry_labels, split_for
from telephony_voice_simulator.datasets.voicemail.export import (
    EXPORT_FIELDS,
    benchmark_rows,
    export_rows,
)
from telephony_voice_simulator.datasets.voicemail.scoring import (
    BASELINE_MAP,
    baseline_predictions,
    format_report,
    score,
)
from telephony_voice_simulator.domains.amd.validation import validate_scenario

CATALOG = load_catalog()


def _raw(**entry) -> dict:
    base = {
        "id": "demo_01", "category": "demo", "prevalence": "common", "difficulty": "easy",
        "language": "en",
        "failure_modes": ["early_message"],
        "provenance": {"basis": "invented", "added_in": 1},
        "segments": [
            {"say": {"text": "Leave a message.", "voice": "female"}},
            {"tone": {"freq": 650, "duration": 0.5, "role": "beep"}},
            {"hold": 30},
        ],
    }
    return {
        "version": 2,
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
        ({"segments": [{"say": {"text": "Hi", "voice": "male"}}, {"hold": 30}]}, "beep-role tone"),
        ({"segments": [
            {"say": {"text": "Hi", "voice": "male"}},
            {"tone": {"freq": 650, "duration": 0.5, "role": "beep"}},
            {"tone": {"freq": 330, "duration": 2.0, "role": "noise"}},
            {"hold": 30},
        ]}, "last tone before the first hold"),
        ({"segments": [
            {"say": {"text": "Hi", "voice": "male"}},
            {"hold": 5},
            {"tone": {"freq": 650, "duration": 0.5, "role": "beep"}},
            {"hold": 30},
        ]}, "last tone before the first hold"),
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
            {"tone": {"freq": 650, "duration": 0.5, "role": "beep"}},
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
    assert all(key.startswith("vmds_") and "/" not in key and len(value) == 2
               for key, value in corpus.items())
    candidate = tmp_path / "public_audio_allowlist.candidate.json"
    assert load_public_allowlist(candidate) == frozenset(corpus)


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
            "message_start_after": {
                "mark": "play_end:vmds_no_beep_after_greeting_01_1.wav", "min": 0.5, "max": 12.0,
            },
        }),
        ("screener_then_voicemail", {
            "callee_class": "machine",
            "ended_reason": "call.ending.voicemail-left-message",
            "message_start_after": {"mark": "tone_end", "min": 0.1, "max": 3.0},
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
        assert actual["agent_speech_after"]["mark"].startswith("play_end:vmds_")


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
    assert result["built"] == ["vmds_carrier_default_01_1.wav"]
    assert (tmp_path / "assets" / "vmds_carrier_default_01_1.wav").read_bytes() == b"wav"
    row = json.loads((tmp_path / "assets_manifest.json").read_text())["vmds_carrier_default_01_1.wav"]
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


def test_catalog_size_and_every_category_has_two_entries() -> None:
    counts = stats(CATALOG)["categories"]
    assert len(CATALOG.entries) >= 125
    assert min(counts.values()) >= 2
    assert CATALOG.version == 2


def test_record_beeps_are_mostly_450_650_750_hz() -> None:
    freqs = [s["tone"]["freq"] for e in CATALOG.entries for s in e.segments
             if "tone" in s and s["tone"]["role"] == "beep"]
    common = sum(f in (450, 650, 750) for f in freqs)
    assert common / len(freqs) > 0.8
    assert freqs.count(1000) <= 3


@pytest.mark.parametrize(
    "entry,message",
    [
        ({"version": 1}, "version: expected 2"),
        ({"segments": [{"say": {"text": "Hi", "voice": "male"}},
                       {"tone": {"freq": 650, "duration": 0.5}}, {"hold": 30}]}, "role"),
        ({"segments": [{"say": {"text": "Hi", "voice": "male"}},
                       {"tone": {"freq": 650, "duration": 0.5, "role": "chime"}}, {"hold": 30}]},
         "tone.role"),
        ({"segments": [{"say": {"text": "Hi", "voice": "male"}},
                       {"tone": {"freq": 350, "duration": 0.2, "role": "noise"}}, {"hold": 30}]},
         "beep-role tone"),
        ({"cues": ["at the tone"]}, "does not occur"),
        ({"cues": ["Leave A Message", "leave a message"]}, None),
        ({"cues": "leave a message"}, "cues"),
        ({"failure_modes": []}, "failure_modes"),
        ({"failure_modes": ["confused"]}, "unknown"),
        ({"failure_modes": ["early_message", "early_message"]}, "duplicate mode"),
        ({"provenance": {"basis": "copied", "added_in": 1}}, "provenance.basis"),
        ({"provenance": {"basis": "invented", "added_in": 3}}, "added_in"),
        ({"provenance": {"basis": "invented"}}, "basis and added_in"),
    ],
)
def test_enriched_fields_are_validated(entry: dict, message: str | None) -> None:
    raw = _raw(**{k: v for k, v in entry.items() if k != "version"})
    if "version" in entry:
        raw["version"] = entry["version"]
    if message is None:
        assert validate_catalog(raw).entries[0].cues == ("Leave A Message", "leave a message")
        return
    with pytest.raises(CatalogError, match=message):
        validate_catalog(raw)


def test_screener_handoff_outcome_needs_speech_and_a_beep() -> None:
    raw = _raw(segments=[{"tone": {"freq": 650, "duration": 0.5, "role": "beep"}}, {"hold": 30}])
    raw["categories"][0].update(expected_class="screener", expected_outcome="leave_message_after_screen")
    with pytest.raises(CatalogError, match="leave_message_after_screen needs speech"):
        validate_catalog(raw)


def test_labels_compute_opening_text_and_structural_timing() -> None:
    labels = {e.id: entry_labels(CATALOG, e) for e in CATALOG.entries}
    # A music bed before the speech does not end the opening; the real beep does.
    assert labels["noisy_or_music_greeting_01"]["opening_text"].startswith("Hey, thanks")
    assert labels["low_tone_before_beep_01"]["timing"]["beep_freqs_hz"] == [650]
    gate = labels["dtmf_gate_press_key_01"]
    assert gate["opening_text"].endswith("press five now.")
    assert "Who's this?" in gate["full_text"] and "Who's this?" not in gate["opening_text"]
    assert labels["late_greeting_after_silence_01"]["timing"]["leading_silence_s"] == 8.3
    readback = labels["carrier_readback_long_pause_01"]
    assert readback["timing"]["longest_pause_s"] == 5.0
    assert readback["opening_text"].endswith("you may hang up.")
    assert labels["unusual_beep_02"]["timing"]["beep_count"] == 2
    assert (labels["tone_only_01"]["opening_text"], labels["tone_only_01"]["opening_words"]) == ("", 0)
    assert labels["screener_then_voicemail_01"]["gold_amd"] == "machine-ivr"
    assert labels["human_short_hello_01"]["gold_amd"] == "human"
    assert labels["carrier_default_01"]["split"] == "test"
    assert labels["carrier_default_02"]["split"] == "dev"
    dev = sum(row["split"] == "dev" for row in labels.values()) / len(labels)
    assert 0.6 < dev < 0.85
    assert split_for("carrier_default_01") == "test"


def test_compile_writes_labels_and_scenario_metadata(tmp_path: Path) -> None:
    from telephony_voice_simulator.telephony.twilio.compile import compile_scenario

    compile_suite(tmp_path, only=["carrier_default_01", "human_short_hello_01"])
    rows = [json.loads(line) for line in (tmp_path / "labels.jsonl").read_text().splitlines()]
    assert [row["id"] for row in rows] == ["carrier_default_01", "human_short_hello_01"]
    for asset in json.loads((tmp_path / "corpus_text.json").read_text()):
        _wav(tmp_path / "assets" / asset, 0.3)
    for path in sorted((tmp_path / "scenarios").glob("*.yaml")):
        scenario = yaml.safe_load(path.read_text())
        assert scenario["metadata"] == next(r for r in rows if r["id"] == scenario["metadata"]["id"])
        assert validate_scenario(scenario, source=path.name) is scenario
        compile_scenario(scenario, assets_dir=tmp_path / "assets", out_dir=tmp_path / "compiled")


def _wav(path: Path, seconds: float) -> None:
    import soundfile as sf

    from telephony_voice_simulator.domains.amd.machine import SAMPLE_RATE, synth_tone

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, synth_tone(300, seconds), SAMPLE_RATE)


def test_whole_dataset_compiles_for_twilio_with_every_expected_mark(tmp_path: Path) -> None:
    from telephony_voice_simulator.telephony.twilio.compile import compile_scenario

    compile_suite(tmp_path)
    assets = tmp_path / "assets"
    for asset in json.loads((tmp_path / "corpus_text.json").read_text()):
        _wav(assets / asset, 0.3)
    for path in sorted((tmp_path / "scenarios").glob("*.yaml")):
        scenario = yaml.safe_load(path.read_text())
        compiled = compile_scenario(scenario, assets_dir=assets, out_dir=tmp_path / "compiled")
        records = [s for seq in compiled.sequences.values() for s in seq if s.terminal == "record"]
        expect = scenario["expect"]
        for key in ("message_start_after", "agent_speech_after"):
            if key in expect:
                assert len(records) == 1, path.name
                assert expect[key]["mark"] in records[0].marks, (path.name, key)
        if "max_overlap_with_playback" in expect:
            played = {s["play"] for steps in scenario["machine"].values()
                      if isinstance(steps, list) for s in steps if "play" in s}
            assert expect["max_overlap_with_playback"]["asset"] in played


def test_export_jsonl_rows_and_benchmark_shape() -> None:
    rows = export_rows(CATALOG)
    assert len(rows) == len(CATALOG.entries)
    assert list(rows[0]) == [*EXPORT_FIELDS, "variant"]
    assert {row["gold_amd"] for row in rows} == {
        "human", "machine-vm", "machine-unavailable", "machine-ivr",
    }
    screener = next(r for r in rows if r["id"] == "call_screener_ai_01")
    assert (screener["expected_class"], screener["callee_class"], screener["gold_amd"]) == (
        "screener", "screener", "machine-ivr",
    )
    bench = benchmark_rows(rows)
    assert bench[0] == {
        "id": rows[0]["id"], "source": "vmds", "gold": rows[0]["gold_amd"],
        "transcript": rows[0]["opening_text"],
    }


def test_export_variants_mimic_stt_output() -> None:
    rows = {r["id"]: r for r in export_rows(CATALOG, variants=True)}
    plain = rows["carrier_readback_long_pause_01__stt_plain"]
    assert plain["variant"] == "stt_plain"
    assert plain["opening_text"].startswith("the number you have dialed 555 0131 is not available")
    digits = rows["carrier_readback_long_pause_01__stt_digits"]
    assert "five five five zero one three one" in digits["opening_text"]
    clipped = rows["carrier_readback_long_pause_01__stt_clipped"]
    assert clipped["opening_text"].startswith("have dialed five")
    assert "human_short_confirmation_01__stt_clipped" not in rows
    assert "human_short_hello_01__stt_digits" not in rows
    assert "tone_only_01__stt_plain" not in rows
    assert rows["carrier_default_01"]["variant"] == "original"


def _case(cid: str, gold: str, callee: str = "machine", category: str = "c",
          split: str = "dev") -> dict:
    return {"id": cid, "gold_amd": gold, "callee_class": callee, "category": category,
            "difficulty": "hard", "language": "en", "split": split}


def test_score_counts_confusion_errors_and_ids() -> None:
    cases = [
        _case("h1", "human", "human", category="people"),
        _case("h2", "human", "human", category="people", split="test"),
        _case("v1", "machine-vm"),
        _case("s1", "machine-ivr", "screener"),
        _case("u1", "machine-unavailable"),
        _case("gone", "machine-vm"),
    ]
    predictions = [
        {"id": "h1", "predicted": "human", "decided_at_s": 1.0},
        {"id": "h2", "predicted": "machine-vm", "decided_at_s": 3.0},
        {"id": "v1", "predicted": "human"},
        {"id": "s1", "predicted": "machine-vm"},
        {"id": "u1", "predicted": "postpone"},
        {"id": "ghost", "predicted": "screener"},
    ]
    report = score(cases, predictions)
    assert (report["cases"], report["scored"], report["correct"], report["accuracy"]) == (
        6, 5, 1, 0.2,
    )
    assert report["humans_called_machine"] == {"count": 1, "ids": ["h2"]}
    assert report["machines_called_human"] == {"count": 1, "ids": ["v1"]}
    assert report["screeners_called_machine"] == {"count": 1, "ids": ["s1"]}
    assert report["abstentions"] == {"count": 1, "ids": ["u1"]}
    assert report["missing_ids"] == ["gone"] and report["extra_ids"] == ["ghost"]
    assert report["confusion_matrix"]["human"] == {
        "human": 1, "machine-vm": 1, "machine-unavailable": 0, "machine-ivr": 0, "abstain": 0,
    }
    assert report["confusion_matrix"]["machine-unavailable"]["abstain"] == 1
    assert report["by_category"]["people"] == {"n": 2, "correct": 1, "abstained": 0, "accuracy": 0.5}
    assert report["by_split"]["test"]["n"] == 1
    assert report["decided_at_s"] == {"n": 2, "median": 2.0}
    assert "humans_called_machine 1: h2" in format_report(report)


@pytest.mark.parametrize(
    "prediction,message",
    [
        ({"id": "v1", "predicted": "voicemail"}, "predicted must be one of"),
        ({"predicted": "human"}, "without an id"),
        ({"id": "v1", "predicted": "human", "decided_at_s": -1}, "decided_at_s"),
        ({"id": "v1", "predicted": ["machine-vm"]}, "predicted must be one of"),
    ],
)
def test_score_rejects_bad_predictions(prediction: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        score([_case("v1", "machine-vm")], [prediction])


def test_baseline_maps_every_silence_bucket() -> None:
    from telephony_voice_simulator.evaluation import silence

    assert {name for name, _ in silence.RULES} | {"unclassified", "true_silence_no_audio"} == set(
        BASELINE_MAP
    )
    rows = {r["id"]: r for r in baseline_predictions(CATALOG)}
    assert rows["carrier_default_01"]["predicted"] == "machine-vm"
    assert rows["tone_only_01"] == {
        "id": "tone_only_01", "predicted": "uncertain", "bucket": "true_silence_no_audio",
    }


def test_cli_export_baseline_and_score(tmp_path: Path, capsys) -> None:
    cases, bench, preds = tmp_path / "cases.jsonl", tmp_path / "cases.json", tmp_path / "p.jsonl"
    assert main(["voicemail-dataset", "export", "--out", str(cases)]) == 0
    assert main(["voicemail-dataset", "export", "--format", "benchmark", "--variants",
                 "--out", str(bench)]) == 0
    assert len(json.loads(bench.read_text())) > len(CATALOG.entries)
    assert main(["voicemail-dataset", "baseline", "--out", str(preds)]) == 0
    capsys.readouterr()
    assert main(["voicemail-dataset", "score", "--cases", str(cases), "--predictions", str(preds),
                 "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scored"] == len(CATALOG.entries) and report["missing_ids"] == []
    assert main(["voicemail-dataset", "score", "--cases", str(cases), "--predictions", str(preds)]) == 0
    assert capsys.readouterr().out.startswith(f"cases {len(CATALOG.entries)}")
    preds.write_text('{"id": "x", "predicted": "robot"}\n')
    assert main(["voicemail-dataset", "score", "--cases", str(cases), "--predictions", str(preds)]) == 1
