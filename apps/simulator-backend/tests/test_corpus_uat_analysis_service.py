"""Service and CLI wiring for the corpus, UAT and analysis command groups."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telephony_voice_simulator.cli.main import main
from telephony_voice_simulator.evaluation import silence
from telephony_voice_simulator.service import ValidationError, analysis, corpus, uat
from telephony_voice_simulator.service.errors import NotFoundError

PROMPTS = "version: 1\nlicense_note: clean-room\nprompts:\n  welcome:\n    text: Welcome.\n"


def test_check_prompts_reports_count(tmp_path: Path) -> None:
    config = tmp_path / "prompts.yaml"
    config.write_text(PROMPTS)

    assert corpus.check_prompts(config=config) == {
        "valid": True,
        "config": str(config),
        "prompts": 1,
    }


def test_check_prompts_needs_an_explicit_path_when_the_default_is_absent(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="not found"):
        corpus.check_prompts(config=tmp_path / "missing.yaml")


def test_shipped_prompt_source_is_valid() -> None:
    assert corpus.check_prompts()["valid"] is True


def test_render_prompts_rejects_unknown_engine(tmp_path: Path) -> None:
    config = tmp_path / "prompts.yaml"
    config.write_text(PROMPTS)

    with pytest.raises(ValidationError, match="unknown engine"):
        corpus.render_prompts(config=config, output=tmp_path / "out", engine="nope")


def test_build_corpus_replace_imported_requires_force() -> None:
    with pytest.raises(ValidationError, match="requires force"):
        corpus.build_corpus(replace_imported=True)


def test_import_audio_rejects_unknown_asset() -> None:
    with pytest.raises(ValidationError, match="not in CORPUS"):
        corpus.import_audio(
            asset="not-a-real-asset.wav",
            source="/nonexistent.wav",
            source_type="internal_device_capture",
            license_note="test",
        )


def test_validate_public_audio_reports_a_failure_as_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="public AMD audio validation failed"):
        corpus.validate_public_audio(allowlist=tmp_path / "missing.json")


def test_uat_configs_lists_only_uat_workflows() -> None:
    names = [entry["name"] for entry in uat.list_configs()]

    assert "uat.example.yaml" in names
    assert "ivr_prompts.yaml" not in names
    assert "benchmark.example.yaml" not in names


def test_uat_check_and_plan_example_config() -> None:
    checked = uat.check_config(config="config/uat.example.yaml")
    assert checked["valid"] is True
    assert checked["steps"] == ["run_hosted_call"]

    planned = uat.plan(config="uat.example.yaml", scenarios=["dtmf_gate_honored"])
    assert [case["scenario"] for case in planned["cases"]] == ["dtmf_gate_honored"]
    action = json.loads(planned["cases"][0]["steps"][0]["action"])
    assert action["argv"][-4:] == ["hosted", "agent-call", "--scenario", "dtmf_gate_honored"]


def test_uat_unknown_config_and_scenario() -> None:
    with pytest.raises(NotFoundError):
        uat.check_config(config="nope.yaml")
    with pytest.raises(ValidationError, match="no matrix scenarios matched"):
        uat.plan(config="uat.example.yaml", scenarios=["nope"])


def test_uat_run_writes_report_and_streams_progress(tmp_path: Path) -> None:
    config = tmp_path / "uat.yaml"
    config.write_text(
        "version: 1\nartifacts_dir: %s\nmatrix: [alpha]\nsteps:\n"
        "  - name: only\n    adapter: {type: noop}\n" % (tmp_path / "artifacts")
    )
    lines: list[str] = []

    report = uat.run(config=config, output=tmp_path / "report.json", log=lines.append)

    assert (report["total"], report["passed"]) == (1, 1)
    assert json.loads((tmp_path / "report.json").read_text())["passed"] == 1
    assert lines[0] == "[1/1] alpha"


def test_classify_silence_rows_buckets_and_shares() -> None:
    rows = [
        {"call_uid": "1", "user_text": "You've reached the voicemail of Sam"},
        {"call_uid": "2", "user_text": ""},
        {"call_uid": "3", "user_text": "press one for sales"},
        {"call_uid": "4", "user_text": "zzz unknowable"},
    ]

    summary = analysis.classify_silence_rows(rows)

    counts = {item["bucket"]: item["count"] for item in summary["buckets"]}
    assert counts == {
        "carrier_vm_stock": 1,
        "true_silence_no_audio": 1,
        "ivr_menu_press": 1,
        "unclassified": 1,
    }
    assert summary["voicemail_like"]["count"] == 1
    assert summary["any_machine_or_system"]["count"] == 2
    assert summary["true_silence"]["share_percent"] == 25.0
    assert silence.classify(rows) == summary


def test_classify_silence_file_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    path.write_text('{"call_uid": "1", "user_text": "leave a message"}\nnot json\n[1]\n')

    summary = analysis.classify_silence(path)

    assert summary["total"] == 1
    assert len(summary["malformed"]) == 2


def test_classify_silence_rejects_empty_input(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    path.write_text("")
    with pytest.raises(ValidationError, match="no valid input rows"):
        analysis.classify_silence(path)
    with pytest.raises(NotFoundError):
        analysis.classify_silence(tmp_path / "missing.jsonl")


def test_cli_wiring(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    calls = tmp_path / "calls.jsonl"
    calls.write_text('{"call_uid": "1", "user_text": "leave a message"}\n')

    assert main(["analyze", "silence", str(calls)]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1

    assert main(["uat", "check", "--config", "config/uat.example.yaml"]) == 0
    assert main(["corpus", "prompts", "--check"]) == 0
    assert main(["uat", "check", "--config", str(tmp_path / "missing.yaml")]) == 1
