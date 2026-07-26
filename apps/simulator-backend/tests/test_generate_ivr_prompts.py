from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).parent.parent / "scripts" / "generate_ivr_prompts.py"
SPEC = importlib.util.spec_from_file_location("generate_ivr_prompts_under_test", SCRIPT)
assert SPEC and SPEC.loader
prompts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prompts
SPEC.loader.exec_module(prompts)


def test_cached_prompt_round_trip_preserves_generator_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "prompts.yaml"
    config.write_text(
        "version: 1\n"
        "license_note: clean-room\n"
        "prompts:\n"
        "  welcome:\n"
        "    text: Welcome to the test line.\n"
    )
    output = tmp_path / "assets"
    output.mkdir()
    audio = output / "welcome.mp3"
    audio.write_bytes(b"locally-generated-audio")
    original_row = {
        "file": "welcome.mp3",
        "text": "Welcome to the test line.",
        "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "engine": "elevenlabs",
        "model": "eleven_multilingual_v2",
        "voice": "configured-custom-voice",
        "voice_fingerprint": "abc123",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    (output / "manifest.json").write_text(
        json.dumps({"prompts": {"welcome": original_row}})
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_ivr_prompts",
            "--config",
            str(config),
            "--output",
            str(output),
        ],
    )

    assert prompts.main() == 0
    rewritten = json.loads((output / "manifest.json").read_text())
    assert rewritten["prompts"]["welcome"] == original_row


def test_cached_prompt_rejects_checksum_drift(tmp_path: Path) -> None:
    audio = tmp_path / "welcome.mp3"
    audio.write_bytes(b"changed")
    manifest = {
        "prompts": {
            "welcome": {
                "file": "welcome.mp3",
                "text": "Welcome.",
                "sha256": hashlib.sha256(b"original").hexdigest(),
                "engine": "macos",
            }
        }
    }

    with pytest.raises(RuntimeError, match="checksum"):
        prompts._cached_row(
            manifest=manifest,
            name="welcome",
            text="Welcome.",
            output=audio,
        )


def test_elevenlabs_voice_settings_preserve_legacy_controls(
    monkeypatch,
) -> None:
    config = {
        "generation": {
            "elevenlabs": {
                "voice_settings": {
                    "stability": 0.4,
                    "similarity_boost": 0.8,
                    "style": 0.2,
                    "use_speaker_boost": False,
                }
            }
        }
    }

    assert prompts._elevenlabs_voice_settings(config) == {
        "stability": 0.4,
        "similarity_boost": 0.8,
        "style": 0.2,
        "use_speaker_boost": False,
        "speed": 1.0,
    }

    monkeypatch.setenv(
        "ELEVENLABS_VOICE_SETTINGS_JSON",
        '{"stability": 2, "similarity_boost": 0.8, "style": 0.2, '
        '"use_speaker_boost": false}',
    )
    with pytest.raises(RuntimeError, match="stability"):
        prompts._elevenlabs_voice_settings(config)


def test_elevenlabs_v3_uses_robust_neutral_settings() -> None:
    config = {
        "generation": {
            "elevenlabs": {
                "voice_settings": {
                    "stability": 1.0,
                    "style": 0.0,
                    "speed": 1.0,
                }
            }
        }
    }

    assert prompts._elevenlabs_voice_settings(config, "eleven_v3") == {
        "stability": 1.0,
        "style": 0.0,
        "speed": 1.0,
    }
    assert prompts._elevenlabs_seed() == 42
