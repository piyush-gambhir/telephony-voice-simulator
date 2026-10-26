"""Turn EXISTING call recordings into scenario assets (real carrier audio).

Many agent stacks record calls dual-channel (one channel = agent, one = callee) and
living at ``s3://$RECORDINGS_BUCKET/<prefix>/<call_id>/...``. The callee channel
of a real voicemail pickup — greeting, gap, actual carrier beep — is the best
possible scenario audio: real codecs, real noise, real beep frequencies.

For each call id this pulls the recording and extracts the callee channel into
a temporary file. It then delegates to ``import_asset`` so normalization,
stable corpus-name validation, licensing metadata, and the default
``commit_allowed=false`` safety gate cannot be bypassed.

Requires: aws CLI with access to your recordings bucket, and ffmpeg.

Usage:
    telephony-voice-sim corpus from-recording \
        --call-id 019e5663-9116-xxxx --asset greeting_stock_carrier.wav \
        --license-note "Captured on a consented internal carrier test call" [--channel 1] \
        [--start 0.0 --duration 30]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .import_asset import import_audio_asset


def _safe_call_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError(
            "call ID must be 1-128 letters, digits, underscores, or hyphens"
        )
    return value


def import_from_recording(
    *,
    call_id: str,
    asset: str,
    license_note: str,
    channel: int = 1,
    start: float = 0.0,
    duration: float = 0.0,
    transcript: str = "",
    commit_allowed: bool = False,
) -> Path:
    """Pull one call recording from S3 and import its callee channel as ``asset``.

    Raises ``ValueError`` for bad input and ``RuntimeError`` when the recording
    bucket is not configured or holds no usable audio.
    """
    bucket = os.environ.get("RECORDINGS_BUCKET")
    if not bucket:
        raise ValueError("Set RECORDINGS_BUCKET (the agent's recording S3 bucket)")
    if channel not in (0, 1):
        raise ValueError("channel must be 0 or 1")
    try:
        call_id = _safe_call_id(call_id)
    except ValueError as exc:
        raise ValueError(f"Invalid call ID: {exc}") from exc

    key_prefix = f"livekit/{call_id}/"
    listing = subprocess.run(
        [
            "aws",
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            key_prefix,
            "--query",
            "Contents[].Key",
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    try:
        keys = json.loads(listing or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON returned by aws s3api: {exc}") from exc
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
        raise RuntimeError("Unexpected object list returned by aws s3api")
    audio_keys = sorted(
        key
        for key in keys
        if key.startswith(key_prefix)
        and key.lower().endswith((".ogg", ".mp4", ".wav", ".m4a"))
    )
    if not audio_keys:
        raise RuntimeError(
            f"No audio object under s3://{bucket}/{key_prefix} "
            "(object identifiers are not printed)"
        )
    key = audio_keys[0]

    with tempfile.TemporaryDirectory() as tmp:
        # Never incorporate an object key into a local path. S3 keys can contain
        # absolute paths, parent traversals, whitespace, and control characters.
        local = Path(tmp) / f"source{Path(key).suffix.lower()}"
        subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket}/{key}", str(local)],
            check=True,
        )
        extracted = Path(tmp) / "extracted-callee.wav"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(local)]
        if start:
            cmd += ["-ss", str(start)]
        if duration:
            cmd += ["-t", str(duration)]
        cmd += [
            "-map_channel", f"0.0.{channel}",
            str(extracted),
        ]
        subprocess.run(cmd, check=True)
        return import_audio_asset(
            asset=asset,
            source=str(extracted),
            source_type="internal_carrier_capture",
            license_note=license_note,
            transcript=transcript,
            commit_allowed=commit_allowed,
            manifest_source="S3 call recording (bucket and call ID redacted)",
        )
