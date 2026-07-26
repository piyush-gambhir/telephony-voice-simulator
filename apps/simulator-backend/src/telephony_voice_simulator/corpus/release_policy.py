"""Public-release boundary for corpus audio.

Audio files are local/internal inputs unless their exact filename is present in
``public_release_allowlist.json``. The allowlist is intentionally separate
from provenance metadata: a manifest row documents what a file is, while this
file records the repository owner's explicit publication decision.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
from pathlib import Path
from typing import Mapping

import yaml

CORPUS_DIR = Path(__file__).resolve().parent
ALLOWLIST = CORPUS_DIR / "public_release_allowlist.json"
MANIFEST = CORPUS_DIR / "assets_manifest.json"
LOCAL_AUDIO_ENV = "SIMULATOR_INCLUDE_LOCAL_AUDIO"
PUBLIC_ALLOWLIST_ENV = "SIMULATOR_PUBLIC_AUDIO_ALLOWLIST"
PUBLIC_AUDIO_SUFFIXES = {".mp3", ".wav"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


class ReleasePolicyError(ValueError):
    """Raised for a malformed or unsafe public audio policy."""


def local_audio_enabled(environment: Mapping[str, str] | None = None) -> bool:
    """Return whether a developer explicitly enabled local/internal audio."""

    raw = (environment or os.environ).get(LOCAL_AUDIO_ENV, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ReleasePolicyError(
        f"{LOCAL_AUDIO_ENV} must be one of: true, false, 1, 0, yes, no, on, off"
    )


def load_public_allowlist(path: Path | None = None) -> frozenset[str]:
    """Load and strictly validate the public-release filename allowlist."""

    configured = os.environ.get(PUBLIC_ALLOWLIST_ENV, "").strip()
    path = path or (Path(configured).expanduser() if configured else ALLOWLIST)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleasePolicyError(f"cannot read public audio allowlist: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"version", "assets"}:
        raise ReleasePolicyError("public audio allowlist needs only version and assets")
    if payload["version"] != 1:
        raise ReleasePolicyError("unsupported public audio allowlist version")
    assets = payload["assets"]
    if not isinstance(assets, list) or not all(isinstance(item, str) for item in assets):
        raise ReleasePolicyError("public audio allowlist assets must be a string list")
    if len(assets) != len(set(assets)):
        raise ReleasePolicyError("public audio allowlist contains duplicate filenames")
    for asset in assets:
        path_value = Path(asset)
        if (
            path_value.name != asset
            or path_value.suffix.lower() not in PUBLIC_AUDIO_SUFFIXES
        ):
            raise ReleasePolicyError(
                f"public audio allowlist entry must be a plain WAV/MP3 filename: {asset}"
            )
    return frozenset(assets)


def exposed_audio_assets(
    known_assets: set[str], environment: Mapping[str, str] | None = None
) -> frozenset[str]:
    """Return local assets for explicit internal builds, otherwise public only."""

    if local_audio_enabled(environment):
        return frozenset(known_assets)
    allowlisted = load_public_allowlist()
    unknown = allowlisted - known_assets
    if unknown:
        raise ReleasePolicyError(
            f"public audio allowlist references unknown corpus assets: {sorted(unknown)}"
        )
    return allowlisted


def validate_public_pstn_assets(
    *,
    scenarios_dir: Path,
    assets_dir: Path,
    allowlist_path: Path | None = None,
    manifest_path: Path | None = None,
) -> frozenset[str]:
    """Validate every source recording that public compiled TwiML can expose."""

    deployment_policy = allowlist_path is not None
    referenced: set[str] = set()
    for scenario_path in sorted(scenarios_dir.glob("*.yaml")):
        payload = yaml.safe_load(scenario_path.read_text())
        machine = payload.get("machine", {}) if isinstance(payload, dict) else {}
        for steps in machine.values():
            if not isinstance(steps, list):
                continue
            for step in steps:
                if isinstance(step, dict) and "play" in step:
                    referenced.add(str(step["play"]))
        on_dtmf = payload.get("on_dtmf") if isinstance(payload, dict) else None
        if isinstance(on_dtmf, dict):
            switched = machine.get(str(on_dtmf.get("switch")), [])
            for step in switched if isinstance(switched, list) else []:
                if isinstance(step, dict) and "play" in step:
                    referenced.add(str(step["play"]))

    allowlisted = load_public_allowlist(allowlist_path)
    missing_approval = referenced - allowlisted
    if missing_approval:
        raise ReleasePolicyError(
            "AMD scenarios reference audio that is not approved for public exposure: "
            f"{sorted(missing_approval)}"
        )
    try:
        manifest = json.loads((manifest_path or MANIFEST).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleasePolicyError(f"cannot read corpus manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ReleasePolicyError("corpus manifest must be an object keyed by filename")
    for filename in sorted(referenced):
        metadata = manifest.get(filename)
        if not isinstance(metadata, dict):
            raise ReleasePolicyError(f"public AMD asset lacks manifest metadata: {filename}")
        if deployment_policy and (
            not str(metadata.get("source_url", "")).strip()
            or not str(metadata.get("license_note", "")).strip()
        ):
            raise ReleasePolicyError(
                f"deployment-approved AMD asset lacks provenance: {filename}"
            )
        if not deployment_policy and metadata.get("commit_allowed") is not True:
            raise ReleasePolicyError(
                f"public AMD asset lacks explicit manifest approval: {filename}"
            )
        expected = str(metadata.get("sha256", ""))
        path = assets_dir / filename
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ReleasePolicyError(f"cannot read approved AMD asset {filename}: {exc}") from exc
        if not expected or not hmac.compare_digest(actual, expected):
            raise ReleasePolicyError(f"checksum mismatch for public AMD asset: {filename}")
    return frozenset(referenced)
