"""Import a real/public audio asset into the simulator corpus.

The simulator stores speech prompts as 48 kHz mono WAVs. This helper downloads
or reads one audio file, normalizes it with ffmpeg, and updates
``assets_manifest.json`` with source/provenance data.

Usage:

    python -m telephony_voice_simulator.corpus.import_asset \
      --asset ios26_name_reason_screen.wav \
      --source /tmp/iphone-screening.m4a \
      --source-type internal_device_capture \
      --license-note "Recorded from our test iPhone with consent" \
      --transcript "The person you're calling is screening their calls..."

The asset name must already exist in ``build_corpus.CORPUS``. Keeping stable
asset names lets scenarios switch from synthetic to real audio without YAML
churn, while tests still enforce provenance for every prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .build_corpus import ASSETS_DIR, CORPUS, SAMPLE_RATE, SOURCE_MANIFEST

MANIFEST = ASSETS_DIR.parent / "assets_manifest.json"

SOURCE_TYPES = (
    "official_public",
    "permissive_internet",
    "internal_device_capture",
    "internal_carrier_capture",
)


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _source_to_temp(source: str) -> Path:
    suffix = Path(urlparse(source).path if _is_url(source) else source).suffix or ".audio"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    tmp = Path(tmp_name)
    try:
        if _is_url(source):
            with httpx.stream("GET", source, follow_redirects=True, timeout=60) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as out:
                    for chunk in resp.iter_bytes():
                        out.write(chunk)
        else:
            src = Path(source).expanduser()
            if not src.exists():
                raise FileNotFoundError(src)
            shutil.copyfile(src, tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        try:
            import os

            os.close(fd)
        except OSError:
            pass
    return tmp


def _to_48k_mono(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            str(dst),
        ],
        check=True,
    )


def _load_manifest() -> dict:
    source = MANIFEST if MANIFEST.exists() else SOURCE_MANIFEST
    if not source.exists():
        return {}
    return json.loads(source.read_text())


def _write_manifest(manifest: dict) -> None:
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


def import_audio_asset(
    *,
    asset: str,
    source: str,
    source_type: str,
    license_note: str,
    transcript: str = "",
    commit_allowed: bool = False,
    manifest_source: str | None = None,
) -> Path:
    """Normalize one asset and record the provenance required by corpus tests."""
    if asset not in CORPUS:
        raise ValueError(
            f"{asset!r} is not in CORPUS. Add it there first so scenarios and tests have a stable key."
        )
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unsupported source type: {source_type}")
    if not license_note.strip():
        raise ValueError("license_note must explain the asset's provenance and use rights")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()
    imported = _source_to_temp(source)
    out = ASSETS_DIR / asset
    with tempfile.NamedTemporaryFile(
        suffix=".wav", prefix=".import-", dir=ASSETS_DIR, delete=False
    ) as temporary:
        normalized = Path(temporary.name)
    try:
        _to_48k_mono(imported, normalized)
        if normalized.stat().st_size <= 44:
            raise ValueError("normalized audio is empty")
        normalized.replace(out)
    finally:
        imported.unlink(missing_ok=True)
        normalized.unlink(missing_ok=True)

    voice_hint, corpus_text = CORPUS[asset]
    transcript = transcript.strip()
    manifest[asset] = {
        "source_type": source_type,
        "source_url": manifest_source or source,
        "license_note": license_note.strip(),
        "transcript_source": "manifest" if transcript else "corpus",
        "voice_hint": voice_hint,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "commit_allowed": bool(commit_allowed),
    }
    if transcript:
        manifest[asset]["transcript"] = transcript
    else:
        del corpus_text  # documents that CORPUS is the transcript authority

    _write_manifest(manifest)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a real/public simulator audio asset")
    parser.add_argument("--asset", required=True, help="Existing CORPUS asset filename to replace")
    parser.add_argument("--source", required=True, help="Local path or http(s) URL")
    parser.add_argument(
        "--source-type",
        required=True,
        choices=SOURCE_TYPES,
        help="Where this audio came from",
    )
    parser.add_argument("--license-note", required=True)
    parser.add_argument(
        "--transcript",
        default="",
        help="Exact transcript for real/imported audio. Defaults to CORPUS text.",
    )
    parser.add_argument(
        "--commit-allowed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether this audio may be committed to the repo",
    )
    args = parser.parse_args()

    try:
        out = import_audio_asset(
            asset=args.asset,
            source=args.source,
            source_type=args.source_type,
            license_note=args.license_note,
            transcript=args.transcript,
            commit_allowed=args.commit_allowed,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"imported {args.asset} from {args.source} -> {out}")
    print(f"updated {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
