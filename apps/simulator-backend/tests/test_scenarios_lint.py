"""Every scenario YAML must be well-formed and reference known vocabulary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import yaml

SIMULATOR = Path(__file__).parent.parent / "src" / "telephony_voice_simulator"
SCENARIOS = sorted((SIMULATOR / "scenarios").glob("*.yaml"))
ASSET_MANIFEST = SIMULATOR / "corpus" / "assets_manifest.json"
PUBLIC_ALLOWLIST = SIMULATOR / "corpus" / "public_release_allowlist.json"

STEP_KEYS = {"wait", "hold", "play", "tone", "repeat_from", "hangup", "dial"}
EXPECT_KEYS = {
    "webhook_received",
    "ended_reason",
    "ended_reason_any_of",
    "ended_reason_not",
    "detection_layer_prefix_any_of",
    "detection_layer_absent",
    "dtmf_received",
    "dtmf_press_count_max",
    "agent_spoke",
    "first_agent_speech_after",
    "agent_speech_after",
    "message_start_after",
    "max_overlap_with_playback",
    "max_overlap_between_marks",
    "message_content",
}


def test_scenarios_exist() -> None:
    assert len(SCENARIOS) >= 10


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
    assert not allowlisted, "the initial public release must not ship corpus binaries"
    assert all(row["commit_allowed"] is False for row in manifest.values())


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


def test_scenarios_are_valid() -> None:
    corpus_names = set()
    from telephony_voice_simulator.corpus.build_corpus import CORPUS

    corpus_names.update(CORPUS.keys())
    manifest_names = set(json.loads(ASSET_MANIFEST.read_text()))

    for path in SCENARIOS:
        scenario = yaml.safe_load(path.read_text())
        assert {"name", "machine", "expect"} <= set(scenario), path.name
        machine = scenario["machine"]
        assert "main" in machine, f"{path.name}: machine needs a 'main' sequence"
        for seq_name, steps in machine.items():
            if seq_name == "on_dtmf":
                assert steps == "ignore" or "switch" in steps, path.name
                continue
            if seq_name == "max_duration":
                assert isinstance(steps, (int, float)) and steps > 0, path.name
                continue
            for step in steps:
                keys = set(step) & STEP_KEYS
                assert keys, f"{path.name}: unknown step {step!r}"
                if "play" in step:
                    assert step["play"] in corpus_names, (
                        f"{path.name}: asset {step['play']} not in corpus manifest"
                    )
                    assert step["play"] in manifest_names, (
                        f"{path.name}: asset {step['play']} missing provenance manifest row"
                    )
        unknown = set(scenario["expect"]) - EXPECT_KEYS
        assert not unknown, f"{path.name}: unknown expect keys {unknown}"
        # on_dtmf switch targets must exist
        on_dtmf = machine.get("on_dtmf")
        if isinstance(on_dtmf, dict):
            assert on_dtmf["switch"] in machine, f"{path.name}: missing switch target"
