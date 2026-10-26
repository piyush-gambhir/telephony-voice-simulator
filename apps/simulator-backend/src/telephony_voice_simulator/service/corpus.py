"""Audio corpus: build, import, validate for public serving, and IVR prompts.

Building and rendering call paid TTS APIs and are long-running.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..corpus import build, from_recordings, import_asset, prompts
from ..corpus.release_policy import ReleasePolicyError, validate_public_pstn_assets
from ..paths import ASSETS_DIR, CORPUS_MANIFEST, SCENARIOS_DIR
from .errors import ConflictError, ValidationError

SOURCE_TYPES = import_asset.SOURCE_TYPES
PROMPT_ENGINES = ("auto", *prompts.ENGINES)


def build_corpus(
    *,
    only: str = "",
    force: bool = False,
    replace_imported: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Render missing corpus audio through TTS (paid APIs).

    Returns the built and cached asset names plus the assets directory and manifest.
    """
    if replace_imported and not force:
        raise ValidationError("replace_imported requires force")
    try:
        return build.build_assets(
            only=only, force=force, replace_imported=replace_imported, log=log
        )
    except RuntimeError as exc:
        raise ConflictError(str(exc)) from exc


def import_audio(
    *,
    asset: str,
    source: str,
    source_type: str,
    license_note: str,
    transcript: str = "",
    commit_allowed: bool = False,
) -> dict[str, Any]:
    """Normalize a local file or URL into a corpus asset and record its provenance."""
    try:
        out = import_asset.import_audio_asset(
            asset=asset,
            source=source,
            source_type=source_type,
            license_note=license_note,
            transcript=transcript,
            commit_allowed=commit_allowed,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return {"asset": asset, "source": source, "path": str(out), "manifest": str(import_asset.MANIFEST)}


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
) -> dict[str, Any]:
    """Import the callee channel of an S3 call recording as a corpus asset."""
    try:
        out = from_recordings.import_from_recording(
            call_id=call_id,
            asset=asset,
            license_note=license_note,
            channel=channel,
            start=start,
            duration=duration,
            transcript=transcript,
            commit_allowed=commit_allowed,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except RuntimeError as exc:
        raise ConflictError(str(exc)) from exc
    return {"asset": asset, "path": str(out), "channel": channel}


def validate_public_audio(
    *,
    allowlist: Path,
    assets_dir: Path = ASSETS_DIR,
    manifest: Path = CORPUS_MANIFEST,
    scenarios_dir: Path = SCENARIOS_DIR,
) -> dict[str, Any]:
    """Check that every audio file the public AMD runtime may serve is approved."""
    try:
        approved = validate_public_pstn_assets(
            scenarios_dir=scenarios_dir,
            assets_dir=assets_dir,
            allowlist_path=allowlist,
            manifest_path=manifest,
        )
    except (OSError, ReleasePolicyError) as exc:
        raise ValidationError(f"public AMD audio validation failed: {exc}") from exc
    return {"valid": True, "referenced_assets": len(approved)}


def check_prompts(*, config: Path | None = None) -> dict[str, Any]:
    """Validate the IVR prompt source without rendering anything."""
    try:
        return prompts.check_prompts(config)
    except ValueError as exc:
        raise ValidationError(f"prompt configuration error: {exc}") from exc


def render_prompts(
    *,
    config: Path | None = None,
    output: Path | None = None,
    engine: str = "auto",
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running when it renders (paid TTS APIs). Reuses checksum-verified cached audio.

    Returns the generated and cached file names plus the manifest path.
    """
    try:
        return prompts.render_prompts(
            config_path=config, output_dir=output, engine=engine, force=force, log=log
        )
    except ValueError as exc:
        raise ValidationError(f"prompt configuration error: {exc}") from exc
    except RuntimeError as exc:
        raise ConflictError(str(exc)) from exc
