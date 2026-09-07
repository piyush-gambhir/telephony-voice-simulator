"""Every scenario YAML must be well-formed and reference known vocabulary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import yaml
import pytest

from telephony_voice_simulator.scenario_validation import validate_scenario

SIMULATOR = Path(__file__).parent.parent / "src" / "telephony_voice_simulator"
SCENARIOS = sorted((SIMULATOR / "scenarios").glob("*.yaml"))
ASSET_MANIFEST = SIMULATOR / "corpus" / "assets_manifest.json"
PUBLIC_ALLOWLIST = SIMULATOR / "corpus" / "public_release_allowlist.json"

def test_catalog_has_unique_names() -> None:
    assert SCENARIOS, "the bundled scenario catalog is empty"
    names = [yaml.safe_load(path.read_text())["name"] for path in SCENARIOS]
    assert len(names) == len(set(names)), "duplicate scenario names shadow each other"


def test_corpus_assets_have_provenance_manifest() -> None:
    from telephony_voice_simulator.corpus.build_corpus import CORPUS

    manifest = json.loads(ASSET_MANIFEST.read_text())
    assert set(manifest) == set(CORPUS), "assets_manifest.json must track every CORPUS asset exactly"

    allowed_source_types = {
        "generated",
        "official_public",
        "permissive_internet",
        "internal_device_capture",
        "internal_carrier_capture",
    }
    for asset, row in manifest.items():
        assert row.get("source_type") in allowed_source_types, asset
        assert row.get("source_url"), asset
        assert row.get("license_note"), asset
        assert row.get("transcript_source") in {"corpus", "manifest"}, asset
        if row.get("transcript_source") == "corpus":
            assert row.get("voice_hint") == CORPUS[asset][0], asset
        else:
            assert row.get("transcript"), asset
        assert isinstance(row.get("commit_allowed"), bool), asset
        assert re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")), asset
        if row["source_type"] == "generated":
            assert row.get("generator_engine"), asset
            assert row.get("generator_model"), asset
            assert row.get("generator_voice"), asset
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("generated_at", "")), asset


def test_local_corpus_assets_are_valid_when_present() -> None:
    import soundfile as sf

    from telephony_voice_simulator.corpus.build_corpus import CORPUS

    assets_dir = SIMULATOR / "corpus" / "assets"
    manifest = json.loads(ASSET_MANIFEST.read_text())
    for asset in CORPUS:
        path = assets_dir / asset
        if not path.is_file():
            continue
        assert sf.info(path).frames > 0, f"{asset}: corpus source audio is empty"
        if expected_digest := manifest[asset].get("sha256"):
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_digest, (
                f"{asset}: source audio no longer matches its provenance checksum"
            )


def test_public_release_audio_is_explicitly_allowlisted() -> None:
    from telephony_voice_simulator.corpus.release_policy import load_public_allowlist

    manifest = json.loads(ASSET_MANIFEST.read_text())
    allowlisted = load_public_allowlist(PUBLIC_ALLOWLIST)
    assert allowlisted <= manifest.keys(), "public audio must have provenance"
    for asset in allowlisted:
        assert manifest[asset]["commit_allowed"] is True, f"{asset}: missing release approval"


def test_privacy_scrubbed_prompts_are_reserved_generic_and_checksum_bound() -> None:
    from telephony_voice_simulator.corpus.build_corpus import CORPUS

    manifest = json.loads(ASSET_MANIFEST.read_text())
    privacy_assets = {
        "number_readback_vm.wav",
        "spanish_number_readback_vm.wav",
        "number_readback_cant_take_call.wav",
        "greeting_multipart_a.wav",
        "greeting_custom_long.wav",
        "greeting_jet_ski.wav",
        "gatekeeper_take_message.wav",
        "reached_name_leave_message.wav",
    }
    source_files = [
        SIMULATOR / "corpus" / "build_corpus.py",
        SIMULATOR / "scenarios" / "05_jet_ski_weird_greeting.yaml",
        SIMULATOR / "scenarios" / "13_pixel_call_screen.yaml",
        SIMULATOR / "scenarios" / "22_google_voice_voicemail.yaml",
        Path(__file__).parents[3] / "scripts" / "generate_content.py",
    ]
    combined = (
        " ".join(CORPUS[asset][1] for asset in privacy_assets)
        + " "
        + " ".join(path.read_text() for path in source_files)
    ).lower()

    assert not re.search(r"\b(mike|dave|tom)\b", combined)
    assert "jet ski holiday" not in combined
    assert "catch you on the flip side" not in combined
    assert "verbatim prompt" not in combined
    assert "verbatim opening" not in combined
    assert "the google subscriber you have dialed" not in combined
    for production_number in (
        "nine five four",
        "three four seven",
        "three one zero seven one seven",
    ):
        assert production_number not in combined
    for asset in privacy_assets:
        row = manifest[asset]
        assert row.get("generator_engine") in {"ElevenLabs", "OpenAI TTS", "macOS say"}
        assert row.get("generator_model")
        assert row.get("generator_voice")
        if row.get("generator_engine") == "ElevenLabs":
            assert re.fullmatch(
                r"configured-voice-[0-9a-f]{12}",
                row.get("generator_voice", ""),
            )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("generated_at", ""))
        assert re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", ""))

    template = json.loads((SIMULATOR / "templates" / "outbound.metadata.json").read_text())
    assert template["metadata"]["customer_name"] == "Sample Contact"


@pytest.mark.parametrize("path", SCENARIOS, ids=lambda path: path.stem)
def test_scenarios_are_valid(path: Path) -> None:
    from telephony_voice_simulator.corpus.build_corpus import CORPUS

    manifest_names = set(json.loads(ASSET_MANIFEST.read_text()))
    scenario = validate_scenario(yaml.safe_load(path.read_text()), source=path.name)
    for steps in scenario["machine"].values():
        if not isinstance(steps, list):
            continue
        for step in steps:
            if "play" in step:
                assert step["play"] in CORPUS, f"{path.name}: asset not in corpus"
                assert step["play"] in manifest_names, f"{path.name}: missing asset provenance"
