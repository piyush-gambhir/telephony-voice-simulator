"""UAT matrix: discover, validate, plan and run config-driven workflows."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ..evaluation import uat
from ..paths import UAT_CONFIG_DIR
from .errors import NotFoundError, ValidationError


def _is_uat_config(path: Path) -> dict[str, Any] | None:
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(raw, dict) and "matrix" in raw and ("steps" in raw or "trigger" in raw):
        return raw
    return None


def list_configs() -> list[dict[str, Any]]:
    """UAT workflow configs in the config directory (other YAML files are skipped)."""
    configs = []
    if UAT_CONFIG_DIR.is_dir():
        for path in sorted(UAT_CONFIG_DIR.glob("*.y*ml")):
            if _is_uat_config(path) is not None:
                configs.append({"name": path.name, "path": str(path)})
    return configs


def _resolve(config: str | Path) -> Path:
    path = Path(config).expanduser()
    if not path.is_file() and not path.is_absolute():
        candidate = UAT_CONFIG_DIR / path
        if candidate.is_file():
            return candidate
    if not path.is_file():
        raise NotFoundError(f"UAT config not found: {config}")
    return path


def check_config(*, config: str | Path) -> dict[str, Any]:
    """Validate a config; returns its scenarios and step names."""
    try:
        return uat.check_config(_resolve(config))
    except uat.ConfigError as exc:
        raise ValidationError(f"configuration error: {exc}") from exc


def plan(*, config: str | Path, scenarios: list[str] | None = None) -> dict[str, Any]:
    """Dry run: every rendered action per scenario, executing nothing."""
    try:
        return uat.plan_matrix(_resolve(config), scenarios)
    except uat.ConfigError as exc:
        raise ValidationError(f"configuration error: {exc}") from exc


def run(
    *,
    config: str | Path,
    scenarios: list[str] | None = None,
    output: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Execute the matrix and write a JSON report.

    Returns the report (total, passed, per-scenario results, report path).
    """
    try:
        return uat.run_matrix(_resolve(config), scenarios, output, log)
    except uat.ConfigError as exc:
        raise ValidationError(f"configuration error: {exc}") from exc
