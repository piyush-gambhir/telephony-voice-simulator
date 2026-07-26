from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from telephony_voice_simulator.corpus import from_recordings


def test_recording_capture_delegates_to_provenance_gated_importer(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if command[:3] == ["aws", "s3api", "list-objects-v2"]:
            return SimpleNamespace(
                stdout=json.dumps(["livekit/private-call-id/call with spaces.wav"])
            )
        if command[:3] == ["aws", "s3", "cp"]:
            Path(command[-1]).write_bytes(b"source")
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"extracted")
        return SimpleNamespace(stdout="")

    def fake_import(**kwargs: object) -> Path:
        source = Path(str(kwargs["source"]))
        assert source.read_bytes() == b"extracted"
        captured.update(kwargs)
        return tmp_path / str(kwargs["asset"])

    monkeypatch.setenv("RECORDINGS_BUCKET", "private-recordings")
    monkeypatch.setattr(from_recordings.subprocess, "run", fake_run)
    monkeypatch.setattr(from_recordings, "import_audio_asset", fake_import)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "from_recordings",
            "--call-id",
            "private-call-id",
            "--asset",
            "greeting_stock_carrier.wav",
            "--license-note",
            "Consented internal test capture",
        ],
    )

    assert from_recordings.main() == 0
    assert captured["source_type"] == "internal_carrier_capture"
    assert captured["license_note"] == "Consented internal test capture"
    assert captured["commit_allowed"] is False
    assert captured["manifest_source"] == "S3 call recording (bucket and call ID redacted)"


def test_recording_capture_rejects_path_traversal_before_aws(
    monkeypatch,
) -> None:
    called = False

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal called
        called = True
        return SimpleNamespace(stdout="[]")

    monkeypatch.setenv("RECORDINGS_BUCKET", "private-recordings")
    monkeypatch.setattr(from_recordings.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "from_recordings",
            "--call-id",
            "../../outside",
            "--asset",
            "greeting_stock_carrier.wav",
            "--license-note",
            "Consented internal test capture",
        ],
    )

    assert from_recordings.main() == 2
    assert called is False
