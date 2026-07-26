from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from telephony_voice_simulator.pstn.deploy_twilio import (
    validate_public_audio_deployment,
)


def fixture_files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    scenarios = tmp_path / "scenarios"
    assets = tmp_path / "assets"
    scenarios.mkdir()
    assets.mkdir()
    audio = assets / "reviewed.wav"
    audio.write_bytes(b"rights-reviewed-audio")
    (scenarios / "fixture.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "fixture",
                "machine": {"main": [{"play": "reviewed.wav"}, {"hangup": True}]},
                "expect": {},
            }
        )
    )
    manifest = tmp_path / "assets_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "reviewed.wav": {
                    "source_url": "internal rights record",
                    "license_note": "Approved for this public deployment",
                    "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                    "commit_allowed": False,
                }
            }
        )
    )
    allowlist = tmp_path / "deployment_allowlist.json"
    allowlist.write_text(json.dumps({"version": 1, "assets": ["reviewed.wav"]}))
    return scenarios, assets, manifest, allowlist


def test_hosted_audio_deploy_requires_acknowledgement_and_allowlist(
    tmp_path: Path,
) -> None:
    scenarios, assets, manifest, allowlist = fixture_files(tmp_path)

    with pytest.raises(ValueError, match="acknowledge-public-audio"):
        validate_public_audio_deployment(
            acknowledged=False,
            allowlist_path=allowlist,
            scenarios_dir=scenarios,
            assets_dir=assets,
            manifest_path=manifest,
        )
    allowlist.write_text(json.dumps({"version": 1, "assets": []}))
    with pytest.raises(ValueError, match="absent from deployment allowlist"):
        validate_public_audio_deployment(
            acknowledged=True,
            allowlist_path=allowlist,
            scenarios_dir=scenarios,
            assets_dir=assets,
            manifest_path=manifest,
        )


def test_hosted_audio_deploy_accepts_checksum_bound_reviewed_source(
    tmp_path: Path,
) -> None:
    scenarios, assets, manifest, allowlist = fixture_files(tmp_path)

    assert validate_public_audio_deployment(
        acknowledged=True,
        allowlist_path=allowlist,
        scenarios_dir=scenarios,
        assets_dir=assets,
        manifest_path=manifest,
    ) == frozenset({"reviewed.wav"})
