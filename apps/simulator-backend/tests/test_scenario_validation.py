from __future__ import annotations

from pathlib import Path

import pytest

from telephony_voice_simulator.domains.amd.validation import asset_path, validate_scenario


@pytest.mark.parametrize("machine, error", [
    ({"main": [{"wait": -1}]}, "nonnegative"),
    ({"main": [{"tone": {"duration": float("inf")}}]}, "finite"),
    ({"main": [{"wait": 1, "hangup": True}]}, "exactly one"),
    ({"main": [{"wait": 1}, {"repeat_from": -1}]}, "earlier step"),
    ({"main": [{"repeat_from": 0}]}, "earlier step"),
    ({"main": [{"play": "../private.wav"}]}, "relative corpus"),
    ({"main": [], "../escape": []}, "sequence name"),
    ({"main": [], "max_duration": True}, "expected a number"),
    ({"main": [], "on_dtmf": {"switch": "missing"}}, "target sequence"),
    ({"main": [], "on_dtmf": {"switch": "main", "digits": []}}, "DTMF keys"),
    ({"main": [{"reject": "maybe"}]}, "busy or rejected"),
    ({"main": [{"wait": 1}, {"reject": "busy"}]}, "first main step"),
    ({"main": [], "other": [{"reject": "busy"}]}, "first main step"),
])
def test_invalid_machine_rejected_before_execution(machine, error):
    with pytest.raises(ValueError, match=error):
        validate_scenario({"name": "custom", "machine": machine, "expect": {}})


@pytest.mark.parametrize("expect, error", [
    ({"agent_speaks": True}, "unknown checks"),
    ({"agent_spoke": "false"}, "true or false"),
    ({"message_start_after": {"min": 5, "max": 1}}, "min must not exceed"),
    ({"message_content": {"expected": "Hello", "min_recall": 2}}, "must not exceed 1"),
    ({"transcript_contains": {"phrases": []}}, "nonempty list"),
    ({"transcript_contains": {"phrases": ["a"], "min_hits": 2}}, "min_hits"),
    ({"transcript_excludes": {"phrases": ["a"], "min_hits": 1}}, "unknown transcript options"),
])
def test_invalid_expectations_cannot_silently_pass(expect, error):
    with pytest.raises(ValueError, match=error):
        validate_scenario({"name": "custom", "machine": {"main": []}, "expect": expect})


def test_error_identifies_scenario_source():
    with pytest.raises(ValueError, match="custom.yaml: name"):
        validate_scenario({"name": "../outside"}, source="custom.yaml")


def test_asset_symlink_cannot_escape_corpus(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    secret = tmp_path / "outside.wav"
    secret.touch()
    (corpus / "prompt.wav").symlink_to(secret)
    with pytest.raises(ValueError, match="escapes the corpus"):
        asset_path(corpus, "prompt.wav")


def test_reject_requires_the_pstn_transport():
    from telephony_voice_simulator.domains.amd.validation import validate_machine

    with pytest.raises(ValueError, match="PSTN transport"):
        validate_machine({"main": [{"reject": "busy"}]}, allow_dial=False)


def test_transfer_target_is_pstn_only():
    with pytest.raises(ValueError, match="PSTN transport"):
        validate_scenario({"name": "t", "transfer_target": True, "machine": {"main": []}, "expect": {}})
