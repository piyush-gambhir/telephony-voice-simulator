from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from telephony_voice_simulator.corpus import build_corpus


def test_builder_binds_generated_audio_to_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    assets = tmp_path / "assets"
    manifest = tmp_path / "assets_manifest.json"
    monkeypatch.setattr(build_corpus, "ASSETS_DIR", assets)
    monkeypatch.setattr(build_corpus, "MANIFEST", manifest)
    monkeypatch.setattr(
        build_corpus,
        "CORPUS",
        {"clean-room.wav": ("female", "A clean-room test prompt.")},
    )

    def fake_tts(_text: str, _voice: str, output: Path) -> dict[str, str]:
        output.write_bytes(b"normalized-wav")
        return {
            "generator_engine": "test TTS",
            "generator_model": "test-model",
            "generator_voice": "test-voice",
        }

    monkeypatch.setattr(build_corpus, "_tts_openai", fake_tts)
    monkeypatch.setattr(sys, "argv", ["build_corpus"])

    assert build_corpus.main() == 0
    audio = assets / "clean-room.wav"
    row = json.loads(manifest.read_text())["clean-room.wav"]
    assert row["sha256"] == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert row["generator_engine"] == "test TTS"
    assert row["generator_model"] == "test-model"
    assert row["generator_voice"] == "test-voice"
    assert row["commit_allowed"] is False


def test_builder_will_not_silently_replace_an_imported_capture(
    tmp_path: Path, monkeypatch
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    manifest = tmp_path / "assets_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "captured.wav": {
                    "source_type": "internal_device_capture",
                    "license_note": "Consented internal test capture",
                    "commit_allowed": False,
                }
            }
        )
    )
    monkeypatch.setattr(build_corpus, "ASSETS_DIR", assets)
    monkeypatch.setattr(build_corpus, "MANIFEST", manifest)
    monkeypatch.setattr(
        build_corpus,
        "CORPUS",
        {"captured.wav": ("female", "A test prompt.")},
    )
    monkeypatch.setattr(sys, "argv", ["build_corpus"])

    assert build_corpus.main() == 1
    assert not (assets / "captured.wav").exists()


def test_elevenlabs_requires_both_key_and_voice_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)

    with pytest.raises(ValueError, match="both ELEVENLABS_API_KEY"):
        build_corpus._tts_elevenlabs("Clean-room prompt.", "female", tmp_path / "out.wav")


def test_elevenlabs_generation_records_only_a_voice_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request: dict[str, object] = {}

    class Response:
        content = b"mock-mp3"

        @staticmethod
        def raise_for_status() -> None:
            return None

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "private-voice-id")

    def fake_post(*_args: object, **kwargs: object) -> Response:
        request.update(kwargs)
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(
        build_corpus,
        "_to_48k_mono",
        lambda _source, output: output.write_bytes(b"normalized-wav"),
    )
    monkeypatch.setattr(build_corpus, "_has_audio", lambda _path: True)

    metadata = build_corpus._tts_elevenlabs(
        "Clean-room prompt.", "female", tmp_path / "out.wav"
    )

    assert metadata is not None
    assert metadata["generator_engine"] == "ElevenLabs"
    assert metadata["generator_model"] == "eleven_v3"
    assert metadata["generator_voice"].startswith("configured-voice-")
    assert metadata["generator_voice_settings"] == {
        "stability": 1.0,
        "style": 0.0,
        "speed": 1.0,
    }
    assert metadata["generator_seed"] == 42
    assert "private-voice-id" not in json.dumps(metadata)
    assert request["json"] == {
        "text": "Clean-room prompt.",
        "model_id": "eleven_v3",
        "voice_settings": {
            "stability": 1.0,
            "style": 0.0,
            "speed": 1.0,
        },
        "seed": 42,
    }


def test_elevenlabs_seed_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_SEED", "-1")

    with pytest.raises(ValueError, match="0 to 4294967295"):
        build_corpus._elevenlabs_seed()
