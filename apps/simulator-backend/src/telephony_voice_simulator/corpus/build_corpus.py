"""Build the scenario audio corpus (48 kHz mono WAVs in ``assets/``).

Greetings are rendered once via TTS and cached — re-running only builds what
is missing. Beeps are synthesized by the machine engine at runtime (``tone:``
steps), so this corpus is speech only.

TTS backends, in order:
  1. ElevenLabs (API key + voice ID set).
  2. OpenAI TTS (OPENAI_API_KEY set).
  3. macOS ``say`` — keyless local fallback so the harness runs anywhere.

Real greetings beat synthetic ones: see ``from_recordings.py`` for pulling the
callee channel out of existing dual-channel call recordings through the
provenance-gated asset importer.

Usage:  python -m telephony_voice_simulator.corpus.build_corpus [--only NAME]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..paths import ASSETS_DIR, CORPUS_DIR, CORPUS_MANIFEST

MANIFEST = CORPUS_MANIFEST
SOURCE_MANIFEST = CORPUS_DIR / "assets_manifest.json"
SAMPLE_RATE = 48000
DEFAULT_ELEVENLABS_MODEL_ID = "eleven_v3"
DEFAULT_ELEVENLABS_SEED = 42
DEFAULT_ELEVENLABS_VOICE_SETTINGS: dict[str, float | bool] = {
    # ElevenLabs' Robust v3 preset: prioritize repeatable, neutral delivery.
    "stability": 1.0,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 1.0,
}
V3_VOICE_SETTING_KEYS = {"stability", "style", "speed"}

# name -> (voice_hint, text). Keep names stable: scenario YAMLs reference them.
CORPUS: dict[str, tuple[str, str]] = {
    "greeting_stock_carrier.wav": (
        "female",
        "Your call has been forwarded to an automated voice messaging system. "
        "The person you are trying to reach is not available. "
        "At the tone, please record your message.",
    ),
    "iphone_live_voicemail_default.wav": (
        "female",
        "Your call has been forwarded to voicemail. "
        "The person you're trying to reach is not available. "
        "At the tone, please record your message.",
    ),
    "greeting_multipart_a.wav": (
        "male",
        "Hello. You reached a test mailbox. Leave a message and we will return your call.",
    ),
    "greeting_multipart_b.wav": (
        "female",
        "Please leave your message after the tone. "
        "When you have finished recording, you may hang up.",
    ),
    "greeting_custom_long.wav": (
        "male",
        "Hello, you have reached an unusually long test greeting. The recipient "
        "cannot answer right now and may be away from the phone for some time. "
        "Please wait until this message is completely finished, then say your "
        "name, the reason for your call, and a callback number twice, slowly.",
    ),
    "greeting_jet_ski.wav": (
        "male",
        "You have reached the test line. The recipient is away until next week. "
        "Please leave a message.",
    ),
    "greeting_mailbox_full.wav": (
        "female",
        "The mailbox belonging to the person you are trying to reach is full "
        "and cannot accept messages at this time. Goodbye.",
    ),
    "gate_press5_screening.wav": (
        "female",
        "The number you are trying to reach has a call screening service. "
        "To be connected, press five now.",
    ),
    "gate_press_any_key.wav": (
        "female",
        "Thank you for waiting. To continue your call, press any key.",
    ),
    "gate_press_pound.wav": (
        "female",
        "This call is being screened. To be connected to the person you are "
        "calling, press the pound key now.",
    ),
    "gatekeeper_take_message.wav": (
        "female",
        "Hello? The person you called is not available right now. "
        "Can I take a message?",
    ),
    "screener_connecting.wav": ("female", "Okay, one moment, connecting you now."),
    "human_hello.wav": ("male", "Hello? ... Hello, who's this?"),
    "human_hello_delayed.wav": ("male", "... Hello?"),
    "business_receptionist_human.wav": (
        "female",
        "Good morning, Northstar Service Center. This is Jordan. "
        "How may I direct your call?",
    ),
    # --- Device screeners (the modern AMD landscape) -------------------------
    # Clean-room copy modeling Pixel Call Screen's interaction shape. It is
    # intentionally not a transcript of a vendor prompt.
    "pixel_call_screen.wav": (
        "female",
        "This call is being screened, and the recipient will receive your "
        "response. Please state your name and the purpose of your call.",
    ),
    "pixel_call_screen_followup.wav": (
        "female",
        "Thank you. Please say whether your call is urgent.",
    ),
    "ios26_name_reason_screen.wav": (
        "female",
        "Before the recipient decides whether to answer, record your name and "
        "a short reason for calling.",
    ),
    "ios26_stay_on_line.wav": (
        "female",
        "Your response was sent. Please remain on the line.",
    ),
    "truecaller_assistant.wav": (
        "female",
        "An automated assistant is screening this call. State your name and "
        "the reason you are calling.",
    ),
    # Voice-based screen that uses a TONE — the "after the tone" phrasing is a
    # voicemail-phrase trap on a call that is NOT a voicemail.
    "record_name_screen.wav": (
        "female",
        "To connect your call, please say your name after the tone.",
    ),
    "screen_connecting_thanks.wav": ("female", "Thank you. Connecting you now."),
    # iOS 17+ Live Voicemail: the human can pick up MID-MESSAGE.
    "live_vm_pickup.wav": (
        "male",
        "Oh hey, sorry, I'm here. I just picked up while you were leaving the "
        "message. Who's this?",
    ),
    # --- Carrier edge machines ----------------------------------------------
    "att_mailbox_not_setup.wav": (
        "female",
        "The wireless customer you are calling is not available. "
        "The mailbox is not set up yet. Goodbye.",
    ),
    "number_not_in_service.wav": (
        "female",
        "We're sorry. The number you have dialed is not in service, or has "
        "been disconnected. Please check the number and dial again.",
    ),
    # Verizon-style: post-message key options INSIDE a genuine voicemail — the
    # agent must leave the message and must NOT press anything.
    "verizon_vm_options.wav": (
        "female",
        "At the tone, please record your message. When you have finished "
        "recording, you may hang up, or press one for more options.",
    ),
    "spanish_voicemail.wav": (
        "female",
        "Su llamada ha sido transferida a un sistema automatico de mensajes de "
        "voz. La persona a la que llama no esta disponible. Por favor, deje su "
        "mensaje despues del tono.",
    ),
    # Clean-room copy modeling the Google Voice / Fi voicemail flow without
    # reproducing its branded opening.
    "google_voice_voicemail.wav": (
        "female",
        "The recipient cannot answer this call. After the signal, record your "
        "message. When you are finished, you may hang up.",
    ),
    # Number-readback patterns use only the NANP-reserved 555-0100–0199 range.
    # Digits in the greeting are an AMD trap: they resemble an IVR, but there
    # is no menu.
    "number_readback_vm.wav": (
        "female",
        "The number you have dialed, two zero two, five five five, zero one "
        "zero one, is not available. At the tone, please record your "
        "message. When you have finished recording, you may hang up.",
    ),
    "spanish_number_readback_vm.wav": (
        "female",
        "Por favor, deje su mensaje para dos cero dos cinco cinco cinco cero "
        "uno cero dos despues del tono.",
    ),
    "reached_name_leave_message.wav": (
        "male",
        "Hello. You reached the test mailbox. Leave a message.",
    ),
    "number_readback_cant_take_call.wav": (
        "female",
        "Two zero two five five five zero one zero three can't take your "
        "call now.",
    ),
    # Clean-room PBX voicemail fragments. These deliberately model the timing
    # shape of a segmented enterprise PBX greeting without copying a vendor's
    # recordings, transcript, voice, or beep asset.
    "pbx_extension_announcement.wav": (
        "female",
        "The person at extension two zero one",
    ),
    "pbx_party_unavailable.wav": (
        "female",
        "cannot answer your call right now.",
    ),
    "pbx_leave_message.wav": (
        "female",
        "After the signal, please leave a message.",
    ),
    # AI voice-to-text screener that stays on the line and asks who's calling.
    # This is a screener, not voicemail: hanging up drops a reachable human.
    "robo_transcription_screener.wav": (
        "female",
        "Hi, I'm a virtual assistant. I'll convert your voice to text so the "
        "person you're calling can respond. If you want to continue, please "
        "stay on the call. Just to clarify, who are you, and why are you calling?",
    ),
    "robo_transcription_followup.wav": (
        "female",
        "Sorry, I didn't catch that. Could you say who you are and why you're "
        "calling?",
    ),
}


def _tts_openai(
    text: str, voice_hint: str, out_wav: Path
) -> dict[str, str] | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    import httpx

    voice = "alloy" if voice_hint == "female" else "onyx"
    model = os.environ.get("OPENAI_TTS_MODEL", "tts-1")
    response = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "voice": voice, "input": text, "response_format": "wav"},
        timeout=60,
    )
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(response.content)
        raw = Path(tmp.name)
    _to_48k_mono(raw, out_wav)
    raw.unlink(missing_ok=True)
    if not _has_audio(out_wav):
        return None
    return {
        "generator_engine": "OpenAI TTS",
        "generator_model": model,
        "generator_voice": voice,
    }


def _elevenlabs_voice_settings(model: str) -> dict[str, float | bool]:
    raw = os.environ.get("ELEVENLABS_VOICE_SETTINGS_JSON", "").strip()
    if not raw:
        configured: Any = {}
    else:
        try:
            configured = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "ELEVENLABS_VOICE_SETTINGS_JSON must be valid JSON"
            ) from exc
    if not isinstance(configured, dict):
        raise ValueError("ELEVENLABS_VOICE_SETTINGS_JSON must be a JSON object")
    unknown = set(configured) - set(DEFAULT_ELEVENLABS_VOICE_SETTINGS)
    if unknown:
        raise ValueError(
            f"unsupported ElevenLabs voice setting(s): {', '.join(sorted(unknown))}"
        )
    settings = {**DEFAULT_ELEVENLABS_VOICE_SETTINGS, **configured}
    for key in ("stability", "similarity_boost", "style"):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"ElevenLabs {key} must be a number from 0 to 1")
        if not 0 <= float(value) <= 1:
            raise ValueError(f"ElevenLabs {key} must be from 0 to 1")
        settings[key] = float(value)
    if not isinstance(settings["use_speaker_boost"], bool):
        raise ValueError("ElevenLabs use_speaker_boost must be true or false")
    speed = settings["speed"]
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ValueError("ElevenLabs speed must be a number from 0.7 to 1.2")
    if not 0.7 <= float(speed) <= 1.2:
        raise ValueError("ElevenLabs speed must be from 0.7 to 1.2")
    settings["speed"] = float(speed)
    if model == "eleven_v3":
        return {key: settings[key] for key in V3_VOICE_SETTING_KEYS}
    return settings


def _elevenlabs_seed() -> int:
    raw = os.environ.get("ELEVENLABS_SEED", str(DEFAULT_ELEVENLABS_SEED)).strip()
    try:
        seed = int(raw)
    except ValueError as exc:
        raise ValueError("ELEVENLABS_SEED must be an integer") from exc
    if not 0 <= seed <= 4_294_967_295:
        raise ValueError("ELEVENLABS_SEED must be from 0 to 4294967295")
    return seed


def _tts_elevenlabs(
    text: str, _voice_hint: str, out_wav: Path
) -> dict[str, Any] | None:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if not key and not voice_id:
        return None
    if not key or not voice_id:
        raise ValueError(
            "ElevenLabs generation requires both ELEVENLABS_API_KEY and "
            "ELEVENLABS_VOICE_ID"
        )

    import httpx

    model = os.environ.get(
        "ELEVENLABS_MODEL_ID", DEFAULT_ELEVENLABS_MODEL_ID
    ).strip() or DEFAULT_ELEVENLABS_MODEL_ID
    seed = _elevenlabs_seed()
    voice_settings = _elevenlabs_voice_settings(model)
    response = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{quote(voice_id, safe='')}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": model,
            "voice_settings": voice_settings,
            "seed": seed,
        },
        timeout=90,
    )
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporary:
        temporary.write(response.content)
        source = Path(temporary.name)
    try:
        _to_48k_mono(source, out_wav)
    finally:
        source.unlink(missing_ok=True)
    if not _has_audio(out_wav):
        return None
    voice_fingerprint = hashlib.sha256(voice_id.encode()).hexdigest()[:12]
    return {
        "generator_engine": "ElevenLabs",
        "generator_model": model,
        "generator_voice": f"configured-voice-{voice_fingerprint}",
        "generator_voice_settings": voice_settings,
        "generator_seed": seed,
    }


def _tts_say(
    text: str, voice_hint: str, out_wav: Path
) -> dict[str, str] | None:
    if sys.platform != "darwin":
        return None
    voice = "Samantha" if voice_hint == "female" else "Daniel"
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
        aiff = Path(tmp.name)
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    _to_48k_mono(aiff, out_wav)
    aiff.unlink(missing_ok=True)
    if not _has_audio(out_wav):
        return None
    return {
        "generator_engine": "macOS say",
        "generator_model": "say",
        "generator_voice": voice,
    }


def _has_audio(path: Path) -> bool:
    """Reject header-only files produced when a local TTS service is unavailable."""

    import soundfile as sf

    try:
        valid = sf.info(path).frames > 0
    except (OSError, RuntimeError):
        valid = False
    if not valid:
        path.unlink(missing_ok=True)
    return valid


def _to_48k_mono(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
            "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16", str(dst),
        ],
        check=True,
    )


def _load_manifest() -> dict[str, dict[str, Any]]:
    source = MANIFEST if MANIFEST.exists() else SOURCE_MANIFEST
    if not source.exists():
        return {}
    parsed = json.loads(source.read_text())
    if not isinstance(parsed, dict):
        raise ValueError("assets_manifest.json must contain an object")
    return parsed


def _write_manifest(manifest: dict[str, dict[str, Any]]) -> None:
    """Replace the manifest only after a complete JSON document is on disk."""

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=".assets-manifest-",
        suffix=".json",
        dir=MANIFEST.parent,
        delete=False,
    ) as temporary:
        json.dump(dict(sorted(manifest.items())), temporary, indent=2)
        temporary.write("\n")
        temporary.flush()
        temporary_path = Path(temporary.name)
    temporary_path.replace(MANIFEST)


def _generated_manifest_row(
    *,
    name: str,
    voice_hint: str,
    output: Path,
    metadata: dict[str, str],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    prior_note = str((previous or {}).get("license_note") or "").strip()
    license_note = prior_note or (
        "Clean-room simulator-authored prompt generated locally by TTS. "
        "Review the selected TTS provider's output terms before public release."
    )
    return {
        "source_type": "generated",
        "source_url": "telephony_voice_simulator.corpus.build_corpus.CORPUS",
        "license_note": license_note,
        "transcript_source": "corpus",
        "voice_hint": voice_hint,
        **metadata,
        "generated_at": dt.datetime.now(dt.UTC).date().isoformat(),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        # Public release is controlled by the explicit release allowlist.
        # A local generation command must never grant that permission itself.
        "commit_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="build only assets whose name contains this")
    parser.add_argument("--force", action="store_true", help="rebuild even if present")
    parser.add_argument(
        "--replace-imported",
        action="store_true",
        help="with --force, explicitly replace a captured/imported asset with generated audio",
    )
    args = parser.parse_args()

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        manifest = _load_manifest()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAILED: cannot read {MANIFEST}: {exc}", file=sys.stderr)
        return 1
    built = skipped = 0
    for name, (voice, text) in CORPUS.items():
        if args.only and args.only not in name:
            continue
        out = ASSETS_DIR / name
        previous = manifest.get(name)
        if out.exists() and not args.force:
            if previous and (expected := previous.get("sha256")):
                actual = hashlib.sha256(out.read_bytes()).hexdigest()
                if actual != expected:
                    print(
                        f"FAILED {name}: cached audio does not match its manifest "
                        "checksum; import it or rebuild with --force",
                        file=sys.stderr,
                    )
                    return 1
            skipped += 1
            continue
        if (
            previous
            and previous.get("source_type") != "generated"
            and not args.replace_imported
        ):
            print(
                f"FAILED {name}: refusing to overwrite an imported/captured asset; "
                "pass --force --replace-imported to replace it intentionally",
                file=sys.stderr,
            )
            return 1

        with tempfile.NamedTemporaryFile(
            suffix=".wav", prefix=".corpus-", dir=ASSETS_DIR, delete=False
        ) as temporary:
            generated = Path(temporary.name)
        try:
            metadata = (
                _tts_elevenlabs(text, voice, generated)
                or _tts_openai(text, voice, generated)
                or _tts_say(text, voice, generated)
            )
            if not metadata:
                print(
                    f"FAILED {name}: no TTS backend "
                    "(set ElevenLabs/OpenAI credentials or run on macOS)"
                )
                return 1
            generated.replace(out)
        finally:
            generated.unlink(missing_ok=True)

        manifest[name] = _generated_manifest_row(
            name=name,
            voice_hint=voice,
            output=out,
            metadata=metadata,
            previous=previous,
        )
        try:
            _write_manifest(manifest)
        except OSError as exc:
            print(f"FAILED {name}: cannot update provenance manifest: {exc}", file=sys.stderr)
            return 1
        print(f"built {name} with {metadata['generator_engine']}")
        built += 1
    print(f"done: {built} built, {skipped} cached → {ASSETS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
