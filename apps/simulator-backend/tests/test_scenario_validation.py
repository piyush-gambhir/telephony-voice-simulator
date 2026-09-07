from __future__ import annotations

from pathlib import Path

import pytest

from telephony_voice_simulator.scenario_validation import asset_path, validate_scenario


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
])
def test_invalid_machine_rejected_before_execution(machine, error):
    with pytest.raises(ValueError, match=error):
        validate_scenario({"name": "custom", "machine": machine, "expect": {}})


@pytest.mark.parametrize("expect, error", [
    ({"agent_speaks": True}, "unknown checks"),
    ({"agent_spoke": "false"}, "true or false"),
    ({"message_start_after": {"min": 5, "max": 1}}, "min must not exceed"),
    ({"message_content": {"expected": "Hello", "min_recall": 2}}, "must not exceed 1"),
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
