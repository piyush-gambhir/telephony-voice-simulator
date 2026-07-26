#!/usr/bin/env python3
"""Recursively check installed JavaScript production/tooling dependency graphs."""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import re
import sys
from typing import Any

REPO = Path(__file__).resolve().parent.parent
NOTICES = REPO / "THIRD_PARTY_NOTICES.md"
PROJECTS = {
    "web": (REPO / "apps" / "web-console", ("dependencies",)),
    # twilio-run is a deployment tool, so its devDependency graph is shipped
    # operational functionality and is scanned even though it is not bundled
    # into the deployed Function.
    "twilio": (REPO / "deploy" / "twilio-ivr", ("dependencies", "devDependencies")),
}
PROHIBITED = re.compile(
    r"\b(?:AGPL|GPL|LGPL|SSPL|BUSL|Commons Clause|PolyForm)\b",
    re.IGNORECASE,
)


def _license_string(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " OR ".join(filter(None, (_license_string(item) for item in value)))
    if isinstance(value, dict):
        return _license_string(value.get("type"))
    return ""


def _license_from_file(package_path: Path) -> str:
    for candidate in sorted(package_path.glob("[Ll][Ii][Cc][Ee][Nn][Ss][Ee]*")):
        if not candidate.is_file():
            continue
        text = candidate.read_text(errors="replace")[:8_000]
        if "Apache License" in text and "Version 2.0" in text:
            return "Apache-2.0"
        if "MIT License" in text:
            return "MIT"
        if "ISC License" in text:
            return "ISC"
        if "BSD 3-Clause" in text:
            return "BSD-3-Clause"
    return ""


def _node_modules_ancestor(package_path: Path) -> Path:
    for candidate in package_path.parents:
        if candidate.name == "node_modules":
            return candidate
    raise ValueError(f"cannot locate node_modules ancestor for {package_path}")


def _resolve_dependency(package_path: Path, name: str, project: Path) -> Path | None:
    node_modules = _node_modules_ancestor(package_path)
    candidates = [node_modules / name, project / "node_modules" / name]
    for parent in node_modules.parents:
        if parent.name == "node_modules":
            candidates.append(parent / name)
    for candidate in candidates:
        metadata = candidate / "package.json"
        if metadata.is_file():
            return candidate.resolve()
    return None


def _scan_project(
    project_name: str, project: Path, dependency_sections: tuple[str, ...]
) -> tuple[list[str], list[str], set[str]]:
    root_metadata = json.loads((project / "package.json").read_text())
    direct: set[str] = set()
    for section in dependency_sections:
        direct.update(root_metadata.get(section, {}))

    queue: deque[tuple[str, Path, bool]] = deque()
    errors: list[str] = []
    for name in sorted(direct):
        path = project / "node_modules" / name
        if not (path / "package.json").is_file():
            errors.append(f"{project_name}:{name}: not installed")
            continue
        queue.append((name, path.resolve(), False))

    seen: set[tuple[str, str]] = set()
    rows: list[str] = []
    exceptions: set[str] = set()
    while queue:
        requested_name, package_path, optional = queue.popleft()
        try:
            metadata = json.loads((package_path / "package.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            if not optional:
                errors.append(f"{project_name}:{requested_name}: unreadable metadata: {exc}")
            continue
        name = str(metadata.get("name") or requested_name)
        version = str(metadata.get("version") or "?")
        key = (name, version)
        if key in seen:
            continue
        seen.add(key)

        license_name = _license_string(
            metadata.get("license") or metadata.get("licenses")
        ) or _license_from_file(package_path)
        if not license_name:
            errors.append(f"{project_name}:{name}@{version}: missing license metadata")
        elif PROHIBITED.search(license_name):
            if name.startswith("@img/sharp-libvips-") and license_name == "LGPL-3.0-or-later":
                # Platform libvips binaries are used only by Next's build-time
                # image tooling. The final static nginx image contains neither
                # node_modules nor libvips; keep this exception explicit.
                exceptions.add(f"{name}@{version}: {license_name} (build-only)")
            else:
                errors.append(
                    f"{project_name}:{name}@{version}: restrictive license {license_name!r}"
                )
        rows.append(f"{project_name}:{name}@{version}: {license_name or 'UNKNOWN'}")

        optional_names = set((metadata.get("optionalDependencies") or {}).keys())
        dependencies = dict(metadata.get("dependencies") or {})
        dependencies.update(metadata.get("optionalDependencies") or {})
        for dependency in sorted(dependencies):
            resolved = _resolve_dependency(package_path, dependency, project)
            is_optional = dependency in optional_names
            if resolved is None:
                if not is_optional:
                    errors.append(
                        f"{project_name}:{name}@{version}: missing dependency {dependency}"
                    )
                continue
            queue.append((dependency, resolved, is_optional))

    notice_text = NOTICES.read_text()
    for name in direct:
        if f"`{name}`" not in notice_text:
            errors.append(f"{project_name}:{name}: missing from THIRD_PARTY_NOTICES.md")
    return errors, rows, exceptions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        choices=("all", *PROJECTS),
        default="all",
        help="installed dependency graph to scan",
    )
    args = parser.parse_args()

    selected = PROJECTS if args.project == "all" else {args.project: PROJECTS[args.project]}
    errors: list[str] = []
    rows: list[str] = []
    exceptions: set[str] = set()
    for project_name, (path, sections) in selected.items():
        project_errors, project_rows, project_exceptions = _scan_project(
            project_name, path, sections
        )
        errors.extend(project_errors)
        rows.extend(project_rows)
        exceptions.update(project_exceptions)
    if errors:
        print("JavaScript license check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"JavaScript recursive-license check passed ({len(rows)} packages)")
    for exception in sorted(exceptions):
        print(f"- reviewed exception: {exception}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
