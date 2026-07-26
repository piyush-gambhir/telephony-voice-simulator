from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from telephony_voice_simulator.corpus import import_asset


def test_import_asset_updates_manifest_and_audio(tmp_path: Path, monkeypatch) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    manifest = tmp_path / "assets_manifest.json"
    source = tmp_path / "source.wav"
    fake_audio = b"fake-wav" * 10
    source.write_bytes(fake_audio)

    monkeypatch.setattr(import_asset, "ASSETS_DIR", assets)
    monkeypatch.setattr(import_asset, "MANIFEST", manifest)
    monkeypatch.setattr(
        import_asset,
        "CORPUS",
        {"ios26_name_reason_screen.wav": ("female", "Please state your name and reason.")},
    )
    monkeypatch.setattr(import_asset, "_to_48k_mono", lambda src, dst: shutil.copyfile(src, dst))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_asset",
            "--asset",
            "ios26_name_reason_screen.wav",
            "--source",
            str(source),
            "--source-type",
            "internal_device_capture",
            "--license-note",
            "Recorded from test device with consent",
            "--transcript",
            "The person you're calling is screening their calls.",
            "--commit-allowed",
        ],
    )

    assert import_asset.main() == 0
    assert (assets / "ios26_name_reason_screen.wav").read_bytes() == fake_audio

    row = json.loads(manifest.read_text())["ios26_name_reason_screen.wav"]
    assert row["source_type"] == "internal_device_capture"
    assert row["source_url"] == str(source)
    assert row["license_note"] == "Recorded from test device with consent"
    assert row["transcript_source"] == "manifest"
    assert row["transcript"] == "The person you're calling is screening their calls."
    assert row["voice_hint"] == "female"
    assert row["sha256"] == hashlib.sha256(fake_audio).hexdigest()
    assert row["commit_allowed"] is True
