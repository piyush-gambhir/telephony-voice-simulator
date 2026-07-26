from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from telephony_voice_simulator.corpus import release_policy


def write_policy(path: Path, assets: list[str]) -> None:
    path.write_text(json.dumps({"version": 1, "assets": assets}))


def test_public_allowlist_is_strict_and_defaults_empty(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    write_policy(policy, [])
    assert release_policy.load_public_allowlist(policy) == frozenset()

    write_policy(policy, ["../private.wav"])
    with pytest.raises(release_policy.ReleasePolicyError):
        release_policy.load_public_allowlist(policy)


def test_local_audio_requires_explicit_boolean_opt_in() -> None:
    assert not release_policy.local_audio_enabled({})
    assert release_policy.local_audio_enabled(
        {release_policy.LOCAL_AUDIO_ENV: "true"}
    )
    with pytest.raises(release_policy.ReleasePolicyError):
        release_policy.local_audio_enabled(
            {release_policy.LOCAL_AUDIO_ENV: "sometimes"}
        )


def test_public_exposure_rejects_unknown_allowlist_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = tmp_path / "policy.json"
    write_policy(policy, ["unknown.wav"])
    monkeypatch.setattr(release_policy, "ALLOWLIST", policy)

    with pytest.raises(release_policy.ReleasePolicyError):
        release_policy.exposed_audio_assets({"known.wav"}, {})

    assert release_policy.exposed_audio_assets(
        {"known.wav"}, {release_policy.LOCAL_AUDIO_ENV: "1"}
    ) == frozenset({"known.wav"})


def test_public_pstn_validation_requires_approved_matching_source_assets(
    tmp_path: Path,
) -> None:
    scenarios = tmp_path / "scenarios"
    assets = tmp_path / "assets"
    scenarios.mkdir()
    assets.mkdir()
    scenarios.joinpath("scenario.yaml").write_text(
        """
name: public_test
machine:
  main:
    - play: approved.wav
"""
    )
    audio = assets / "approved.wav"
    audio.write_bytes(b"approved-test-audio")
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    allowlist = tmp_path / "allowlist.json"
    write_policy(allowlist, ["approved.wav"])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "approved.wav": {
                    "commit_allowed": False,
                    "source_url": "https://assets.example.test/approved.wav",
                    "license_note": "Deployment owner reviewed redistribution rights.",
                    "sha256": digest,
                }
            }
        )
    )

    assert release_policy.validate_public_pstn_assets(
        scenarios_dir=scenarios,
        assets_dir=assets,
        allowlist_path=allowlist,
        manifest_path=manifest,
    ) == frozenset({"approved.wav"})

    audio.write_bytes(b"changed")
    with pytest.raises(release_policy.ReleasePolicyError, match="checksum mismatch"):
        release_policy.validate_public_pstn_assets(
            scenarios_dir=scenarios,
            assets_dir=assets,
            allowlist_path=allowlist,
            manifest_path=manifest,
        )
