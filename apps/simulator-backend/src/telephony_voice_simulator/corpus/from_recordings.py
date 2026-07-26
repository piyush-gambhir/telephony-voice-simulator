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
    python -m telephony_voice_simulator.corpus.from_recordings \
        --call-id 019e5663-9116-xxxx --asset greeting_stock_carrier.wav \
        --license-note "Captured on a consented internal carrier test call" [--channel 1] \
        [--start 0.0 --duration 30]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .import_asset import import_audio_asset


def _safe_call_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise ValueError(
            "call ID must be 1-128 letters, digits, underscores, or hyphens"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-id", required=True)
    parser.add_argument(
        "--asset",
        "--name",
        dest="asset",
        required=True,
        help="existing CORPUS asset filename to replace",
    )
    parser.add_argument("--channel", type=int, choices=(0, 1), default=1)
    parser.add_argument("--start", type=float, default=0.0, help="trim start (s)")
    parser.add_argument("--duration", type=float, default=0.0, help="trim length (s), 0 = all")
    parser.add_argument(
        "--license-note",
        required=True,
        help="capture consent, ownership, and permitted-use note",
    )
    parser.add_argument("--transcript", default="", help="exact transcript, if different from CORPUS")
    parser.add_argument(
        "--commit-allowed",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="whether this recording may be committed to the repository",
    )
    args = parser.parse_args()

    bucket = os.environ.get("RECORDINGS_BUCKET")
    if not bucket:
        print("Set RECORDINGS_BUCKET (the agent's recording S3 bucket)", file=sys.stderr)
        return 2

    try:
        call_id = _safe_call_id(args.call_id)
    except ValueError as exc:
        print(f"Invalid --call-id: {exc}", file=sys.stderr)
        return 2

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
        print(f"Invalid JSON returned by aws s3api: {exc}", file=sys.stderr)
        return 1
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
        print("Unexpected object list returned by aws s3api", file=sys.stderr)
        return 1
    audio_keys = sorted(
        key
        for key in keys
        if key.startswith(key_prefix)
        and key.lower().endswith((".ogg", ".mp4", ".wav", ".m4a"))
    )
    if not audio_keys:
        print(
            f"No audio object under s3://{bucket}/{key_prefix} "
            "(object identifiers are not printed)",
            file=sys.stderr,
        )
        return 1
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
        if args.start:
            cmd += ["-ss", str(args.start)]
        if args.duration:
            cmd += ["-t", str(args.duration)]
        cmd += [
            "-map_channel", f"0.0.{args.channel}",
            str(extracted),
        ]
        subprocess.run(cmd, check=True)
        try:
            out = import_audio_asset(
                asset=args.asset,
                source=str(extracted),
                source_type="internal_carrier_capture",
                license_note=args.license_note,
                transcript=args.transcript,
                commit_allowed=args.commit_allowed,
                manifest_source="S3 call recording (bucket and call ID redacted)",
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    print(f"imported {out} from a redacted S3 recording, channel {args.channel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
