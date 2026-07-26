#!/usr/bin/env python3
"""Validate a corpus before the public AMD runtime can expose compiled audio."""

from __future__ import annotations

import argparse
from pathlib import Path

from telephony_voice_simulator.corpus.release_policy import (
    ReleasePolicyError,
    validate_public_pstn_assets,
)
from telephony_voice_simulator.paths import ASSETS_DIR, CORPUS_MANIFEST, SCENARIOS_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allowlist",
        type=Path,
        required=True,
        help="version-1 JSON file listing every approved source WAV/MP3 filename",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=ASSETS_DIR,
        help="directory containing checksum-bound private source audio",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=CORPUS_MANIFEST,
        help="matching assets_manifest.json",
    )
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=SCENARIOS_DIR,
        help="AMD YAML scenario catalog to validate",
    )
    args = parser.parse_args()

    try:
        approved = validate_public_pstn_assets(
            scenarios_dir=args.scenarios_dir,
            assets_dir=args.assets_dir,
            allowlist_path=args.allowlist,
            manifest_path=args.manifest,
        )
    except (OSError, ReleasePolicyError) as exc:
        parser.exit(2, f"public AMD audio validation failed: {exc}\n")

    print(
        "public AMD audio validation passed "
        f"({len(approved)} referenced source asset(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
