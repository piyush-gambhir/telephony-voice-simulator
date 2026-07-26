#!/usr/bin/env python3
"""Check direct runtime Python dependencies for usable license metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
import re
import sys
import tomllib

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "apps" / "simulator-backend" / "pyproject.toml"
PROHIBITED = re.compile(
    r"\b(?:AGPL|GPL|LGPL|SSPL|BUSL|Commons Clause|PolyForm)\b",
    re.IGNORECASE,
)


def dependency_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    if not match:
        raise ValueError(f"cannot parse dependency requirement: {requirement!r}")
    return match.group(1)


def declared_license(name: str) -> tuple[str, str]:
    metadata = distribution(name).metadata
    license_name = (metadata.get("License-Expression") or metadata.get("License") or "").strip()
    if not license_name:
        classifiers = [
            item.removeprefix("License :: OSI Approved :: ").strip()
            for item in metadata.get_all("Classifier", [])
            if item.startswith("License :: OSI Approved :: ")
        ]
        license_name = " AND ".join(classifiers)
    return distribution(name).version, license_name


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    errors: list[str] = []
    rows: list[str] = []
    for requirement in project.get("dependencies", []):
        name = dependency_name(requirement)
        try:
            version, license_name = declared_license(name)
        except PackageNotFoundError:
            errors.append(f"{name}: not installed; run uv sync --frozen")
            continue
        if not license_name:
            errors.append(f"{name}: missing license metadata")
            continue
        if PROHIBITED.search(license_name):
            errors.append(f"{name}: review prohibited/restrictive license {license_name!r}")
        rows.append(f"{name}@{version}: {license_name}")
    if errors:
        print("Python license check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Python direct-license check passed ({len(rows)} packages)")
    for row in rows:
        print(f"- {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
