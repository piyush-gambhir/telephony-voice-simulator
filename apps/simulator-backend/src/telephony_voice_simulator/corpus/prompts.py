"""Generate clean-room IVR prompts for the optional Twilio Functions app.

Run it with ``telephony-voice-sim corpus prompts [--check]``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable

import httpx
import yaml

from ..paths import IVR_PROMPTS_CONFIG, IVR_PROMPTS_OUTPUT_DIR

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


def _elevenlabs_voice_settings(
    config: dict[str, Any], model: str | None = None
) -> dict[str, float | bool]:
    generation = config.get("generation") or {}
    elevenlabs = generation.get("elevenlabs") or {}
    configured: Any = elevenlabs.get(
        "voice_settings", DEFAULT_ELEVENLABS_VOICE_SETTINGS
    )
    if raw_override := os.environ.get("ELEVENLABS_VOICE_SETTINGS_JSON"):
        try:
            configured = json.loads(raw_override)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "ELEVENLABS_VOICE_SETTINGS_JSON must be a JSON object"
            ) from exc
    if not isinstance(configured, dict):
        raise RuntimeError("ElevenLabs voice_settings must be an object")

    unknown = set(configured) - set(DEFAULT_ELEVENLABS_VOICE_SETTINGS)
    if unknown:
        raise RuntimeError(
            f"unsupported ElevenLabs voice setting(s): {', '.join(sorted(unknown))}"
        )
    settings = {**DEFAULT_ELEVENLABS_VOICE_SETTINGS, **configured}
    for key in ("stability", "similarity_boost", "style"):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"ElevenLabs {key} must be a number from 0 to 1")
        if not 0 <= float(value) <= 1:
            raise RuntimeError(f"ElevenLabs {key} must be from 0 to 1")
        settings[key] = float(value)
    if not isinstance(settings["use_speaker_boost"], bool):
        raise RuntimeError("ElevenLabs use_speaker_boost must be true or false")
    speed = settings["speed"]
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise RuntimeError("ElevenLabs speed must be a number from 0.7 to 1.2")
    if not 0.7 <= float(speed) <= 1.2:
        raise RuntimeError("ElevenLabs speed must be from 0.7 to 1.2")
    settings["speed"] = float(speed)
    if model == "eleven_v3":
        return {key: settings[key] for key in V3_VOICE_SETTING_KEYS}
    return settings


def _elevenlabs_seed() -> int:
    raw = os.environ.get("ELEVENLABS_SEED", str(DEFAULT_ELEVENLABS_SEED)).strip()
    try:
        seed = int(raw)
    except ValueError as exc:
        raise RuntimeError("ELEVENLABS_SEED must be an integer") from exc
    if not 0 <= seed <= 4_294_967_295:
        raise RuntimeError("ELEVENLABS_SEED must be from 0 to 4294967295")
    return seed


def _elevenlabs(
    text: str, output: Path, config: dict[str, Any]
) -> dict[str, Any]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        raise RuntimeError("set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID")
    generation = config.get("generation") or {}
    elevenlabs = generation.get("elevenlabs") or {}
    model = os.environ.get(
        "ELEVENLABS_MODEL_ID",
        str(elevenlabs.get("model_id") or DEFAULT_ELEVENLABS_MODEL_ID),
    )
    voice_settings = _elevenlabs_voice_settings(config, model)
    seed = _elevenlabs_seed()
    response = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": model,
            "voice_settings": voice_settings,
            "seed": seed,
        },
        timeout=90,
    )
    response.raise_for_status()
    output.write_bytes(response.content)
    voice_fingerprint = hashlib.sha256(voice_id.encode()).hexdigest()[:12]
    return {
        "engine": "elevenlabs",
        "model": model,
        "voice": "configured-custom-voice",
        "voice_fingerprint": voice_fingerprint,
        "voice_settings": voice_settings,
        "seed": seed,
    }


def _openai(
    text: str, output: Path, _config: dict[str, Any]
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("set OPENAI_API_KEY")
    model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    voice = os.environ.get("OPENAI_TTS_VOICE", "alloy")
    response = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": "mp3",
        },
        timeout=90,
    )
    response.raise_for_status()
    output.write_bytes(response.content)
    return {"engine": "openai", "model": model, "voice": voice}


def _macos(
    text: str, output: Path, _config: dict[str, Any]
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise RuntimeError("the macOS backend is only available on macOS")
    voice = os.environ.get("MACOS_TTS_VOICE", "Samantha")
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as temporary:
        source = Path(temporary.name)
    try:
        subprocess.run(["say", "-v", voice, "-o", str(source), text], check=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-b:a",
                "64k",
                str(output),
            ],
            check=True,
        )
    finally:
        source.unlink(missing_ok=True)
    return {"engine": "macos", "model": "say", "voice": voice}


ENGINES: dict[
    str, Callable[[str, Path, dict[str, Any]], dict[str, Any]]
] = {
    "elevenlabs": _elevenlabs,
    "openai": _openai,
    "macos": _macos,
}


def _has_audio(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return False
    try:
        return float(probe.stdout.strip()) > 0
    except ValueError:
        return False


def _select_engine(requested: str) -> str:
    if requested != "auto":
        return requested
    if os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_VOICE_ID"):
        return "elevenlabs"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if sys.platform == "darwin":
        return "macos"
    raise RuntimeError(
        "no TTS backend available; configure ElevenLabs/OpenAI or run on macOS"
    )


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("prompt configuration version must be 1")
    prompts = config.get("prompts")
    if not isinstance(prompts, dict) or not prompts:
        raise ValueError("prompt configuration must contain prompts")
    for name, prompt in prompts.items():
        if not str(name).replace("_", "").isalnum():
            raise ValueError(f"invalid prompt name: {name}")
        if not isinstance(prompt, dict) or not str(prompt.get("text") or "").strip():
            raise ValueError(f"prompt {name} needs text")
    _elevenlabs_voice_settings(config)
    return config


def _load_prior_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read prior prompt manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("prior prompt manifest must be a JSON object")
    return manifest


def _cached_row(
    *,
    manifest: dict[str, Any],
    name: str,
    text: str,
    output: Path,
) -> dict[str, Any]:
    rows = manifest.get("prompts")
    row = rows.get(name) if isinstance(rows, dict) else None
    if not isinstance(row, dict):
        raise RuntimeError(
            f"{output.name} exists without provenance; regenerate it with --force"
        )
    if row.get("file") != output.name or row.get("text") != text:
        raise RuntimeError(
            f"{output.name} no longer matches its prompt source; regenerate it with --force"
        )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    if row.get("sha256") != digest:
        raise RuntimeError(
            f"{output.name} no longer matches its manifest checksum; regenerate it with --force"
        )
    if row.get("engine") in {None, "", "cached"}:
        raise RuntimeError(
            f"{output.name} has incomplete generator metadata; regenerate it with --force"
        )
    return dict(row)


def _config_path(path: Path | None) -> Path:
    resolved = path or IVR_PROMPTS_CONFIG
    if not resolved.is_file():
        raise ValueError(
            f"prompt configuration not found: {resolved} "
            "(pass an explicit config path outside a source checkout)"
        )
    return resolved


def check_prompts(config_path: Path | None = None) -> dict[str, Any]:
    """Validate the prompt source only; raises ``ValueError`` when it is invalid."""
    path = _config_path(config_path)
    try:
        config = load_config(path)
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        raise ValueError(str(exc)) from exc
    return {"valid": True, "config": str(path), "prompts": len(config["prompts"])}


def render_prompts(
    *,
    config_path: Path | None = None,
    output_dir: Path | None = None,
    engine: str = "auto",
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Render every prompt to MP3 plus a provenance manifest.

    Raises ``ValueError`` for a bad configuration or engine and ``RuntimeError``
    when a prompt cannot be rendered or its cached audio no longer matches.
    """
    emit = log or (lambda _line: None)
    if engine != "auto" and engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}; use auto or one of {', '.join(ENGINES)}")
    config_file = _config_path(config_path)
    try:
        config = load_config(config_file)
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        raise ValueError(str(exc)) from exc

    output_dir = output_dir or IVR_PROMPTS_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    render: Callable[[str, Path, dict[str, Any]], dict[str, Any]] | None = None
    manifest_path = output_dir / "manifest.json"
    try:
        prior_manifest = _load_prior_manifest(manifest_path)
    except RuntimeError as exc:
        raise RuntimeError(f"prompt manifest error: {exc}") from exc
    rows: dict[str, dict[str, Any]] = {}
    generated: list[str] = []
    cached: list[str] = []
    for name, prompt in config["prompts"].items():
        output = output_dir / f"{name.replace('_', '-')}.mp3"
        prompt_text = str(prompt["text"])
        if output.exists() and not force:
            try:
                rows[name] = _cached_row(
                    manifest=prior_manifest,
                    name=name,
                    text=prompt_text,
                    output=output,
                )
            except (OSError, RuntimeError) as exc:
                raise RuntimeError(f"failed {name}: {exc}") from exc
            emit(f"cached {output.name}")
            cached.append(output.name)
            continue
        try:
            if render is None:
                render = ENGINES[_select_engine(engine)]
            metadata = render(prompt_text, output, config)
        except (OSError, RuntimeError, subprocess.CalledProcessError, httpx.HTTPError) as exc:
            raise RuntimeError(f"failed {name}: {exc}") from exc
        if not _has_audio(output):
            output.unlink(missing_ok=True)
            raise RuntimeError(f"failed {name}: TTS backend produced no playable audio")
        emit(f"generated {output.name} with {metadata['engine']}")
        generated.append(output.name)
        rows[name] = {
            "file": output.name,
            "text": prompt_text,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            **metadata,
        }

    manifest = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        # Do not leak a developer's absolute workstation path into a deployed
        # Serverless asset. The digest still makes the source reproducible.
        "source": config_file.name,
        "source_sha256": hashlib.sha256(config_file.read_bytes()).hexdigest(),
        "license_note": str(config.get("license_note") or ""),
        "prompts": rows,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    emit(f"wrote {manifest_path}")
    return {
        "generated": generated,
        "cached": cached,
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
    }
