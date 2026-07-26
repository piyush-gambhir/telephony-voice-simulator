#!/usr/bin/env python3
"""Fail closed when a public artifact exposes unapproved audio."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tomllib
import zipfile

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "apps" / "simulator-backend"
PACKAGE = BACKEND / "src" / "telephony_voice_simulator"
CORPUS = PACKAGE / "corpus"
ASSETS = CORPUS / "assets"
MANIFEST = CORPUS / "assets_manifest.json"
ALLOWLIST = CORPUS / "public_release_allowlist.json"
WEB_AUDIO = REPO / "apps" / "web-console" / "public" / "audio"
WEB_OUT = REPO / "apps" / "web-console" / "out"
AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}
WEB_LEGAL_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")
PRIVATE_DIRECTORY_NAMES = {
    "private",
    "recordings",
    "captures",
    "artifacts",
    "backups",
    "exports",
}
PRIVATE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".dump",
}

sys.path.insert(0, str(BACKEND / "src"))
from telephony_voice_simulator.corpus.release_policy import (  # noqa: E402
    ReleasePolicyError,
    load_public_allowlist,
)


def _audio_names(names: list[str]) -> list[str]:
    return sorted(
        name
        for name in names
        if PurePosixPath(name).suffix.lower() in AUDIO_SUFFIXES
    )


def _validate_allowlist() -> frozenset[str]:
    allowlisted = load_public_allowlist(ALLOWLIST)
    manifest = json.loads(MANIFEST.read_text())
    errors: list[str] = []
    for asset in allowlisted:
        row = manifest.get(asset)
        path = ASSETS / asset
        if not isinstance(row, dict):
            errors.append(f"{asset}: missing assets_manifest.json row")
            continue
        if row.get("commit_allowed") is not True:
            errors.append(f"{asset}: manifest commit_allowed must be true")
        expected = row.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            errors.append(f"{asset}: an allowlisted binary requires a SHA-256")
        elif not path.is_file():
            errors.append(f"{asset}: allowlisted source binary is missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"{asset}: source binary does not match its manifest SHA-256")
    if errors:
        raise ValueError("\n".join(errors))
    return allowlisted


def _validate_package_configuration(allowlisted: frozenset[str]) -> None:
    config = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    package_data = (
        config.get("tool", {})
        .get("setuptools", {})
        .get("package-data", {})
        .get("telephony_voice_simulator", [])
    )
    unsafe = []
    for item in package_data:
        item_path = PurePosixPath(item)
        if (
            "corpus/assets" in item
            and item_path.suffix.lower() in AUDIO_SUFFIXES
            and ("*" in item or "?" in item or item_path.name not in allowlisted)
        ):
            unsafe.append(item)
    if unsafe:
        raise ValueError(f"wheel package-data exposes corpus audio: {unsafe}")


def _validate_publication_files(allowlisted: frozenset[str]) -> None:
    """Inspect tracked files plus every untracked file Git would add.

    Including ``--others --exclude-standard`` keeps this gate effective before
    the initial commit, while still honoring the repository's reviewed ignore
    boundary.
    """

    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    tracked = completed.stdout.decode().split("\0")
    audio_errors: list[str] = []
    private_errors: list[str] = []
    allowed_stems = {Path(asset).stem for asset in allowlisted}
    publication_files = [item for item in tracked if item]
    for relative in _audio_names(publication_files):
        path = PurePosixPath(relative)
        if "corpus/assets" in relative:
            approved = path.name in allowlisted
        elif relative.startswith("apps/web-console/public/audio/"):
            approved = path.stem in allowed_stems
        else:
            approved = False
        if not approved:
            audio_errors.append(relative)

    for relative in publication_files:
        path = PurePosixPath(relative)
        lowered_parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        suffix = path.suffix.lower()
        is_private_env = name == ".env" or (
            name.startswith(".env.") and name != ".env.example"
        )
        is_secret_json = suffix == ".json" and (
            "credential" in name or "secret" in name
        )
        is_database_dump = name.endswith(".sql.gz")
        if (
            lowered_parts & PRIVATE_DIRECTORY_NAMES
            or suffix in PRIVATE_SUFFIXES
            or is_private_env
            or is_secret_json
            or is_database_dump
        ):
            private_errors.append(relative)

    if audio_errors:
        raise ValueError(
            f"Git publication set contains non-allowlisted audio: {audio_errors}"
        )
    if private_errors:
        raise ValueError(
            f"Git publication set contains private configuration or data: {private_errors}"
        )


def _validate_web_output(allowlisted: frozenset[str]) -> None:
    if not WEB_AUDIO.exists():
        return
    allowed_stems = {Path(asset).stem for asset in allowlisted}
    exposed = [
        path.relative_to(REPO).as_posix()
        for path in WEB_AUDIO.rglob("*")
        if path.is_file()
        and path.suffix.lower() in AUDIO_SUFFIXES
        and path.stem not in allowed_stems
    ]
    if exposed:
        raise ValueError(f"static web output exposes non-allowlisted audio: {exposed}")


def _validate_web_legal_files() -> None:
    """Require exact legal notices whenever a static distribution exists."""

    if not WEB_OUT.exists():
        return
    errors: list[str] = []
    for filename in WEB_LEGAL_FILES:
        source = REPO / filename
        distributed = WEB_OUT / filename
        if not distributed.is_file():
            errors.append(f"{filename}: missing from static web distribution")
        elif distributed.read_bytes() != source.read_bytes():
            errors.append(f"{filename}: static copy differs from repository source")
    if errors:
        raise ValueError("; ".join(errors))


def _archive_members(path: Path) -> list[str]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(path) as archive:
            return archive.getnames()
    raise ValueError(f"unsupported release archive: {path}")


def _validate_archive(path: Path, allowlisted: frozenset[str]) -> None:
    allowed_stems = {Path(asset).stem for asset in allowlisted}
    exposed = [
        name
        for name in _audio_names(_archive_members(path))
        if PurePosixPath(name).stem not in allowed_stems
    ]
    if exposed:
        raise ValueError(f"{path}: archive exposes non-allowlisted audio: {exposed}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        action="append",
        default=[],
        help="also inspect a built wheel, zip, or source archive",
    )
    parser.add_argument(
        "--archives-in",
        type=Path,
        action="append",
        default=[],
        help="inspect every wheel/zip/tar archive in this directory",
    )
    args = parser.parse_args()
    try:
        allowlisted = _validate_allowlist()
        _validate_package_configuration(allowlisted)
        _validate_publication_files(allowlisted)
        _validate_web_output(allowlisted)
        _validate_web_legal_files()
        archives = list(args.archive)
        for directory in args.archives_in:
            archives.extend(
                path
                for path in sorted(directory.iterdir())
                if path.suffix in {".whl", ".zip"}
                or path.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz"))
            )
        for archive in archives:
            _validate_archive(archive, allowlisted)
    except (
        OSError,
        ReleasePolicyError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"public-release check failed: {exc}", file=sys.stderr)
        return 1
    print(
        "public-release boundary valid "
        f"({len(allowlisted)} explicitly allowlisted audio asset(s); "
        "static legal files verified when built)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
