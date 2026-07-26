"""Canonical filesystem locations used by the backend."""

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parents[1]

SCENARIOS_DIR = PACKAGE_ROOT / "scenarios"
CORPUS_DIR = PACKAGE_ROOT / "corpus"
TEMPLATES_DIR = PACKAGE_ROOT / "templates"

# An editable source checkout keeps its traditional data/results folders. An
# installed wheel must never try to write into site-packages, so it falls back
# to a working-directory state folder unless the operator selects a path.
_source_checkout = (BACKEND_ROOT / "pyproject.toml").is_file()
_default_runtime_root = (
    BACKEND_ROOT if _source_checkout else Path.cwd() / ".telephony-voice-simulator"
)


def _configured_path(name: str, default: Path) -> Path:
    configured = os.environ.get(name, "").strip()
    return Path(configured).expanduser() if configured else default


RUNTIME_ROOT = _configured_path("SIMULATOR_RUNTIME_DIR", _default_runtime_root)
DATA_DIR = _configured_path("SIMULATOR_DATA_DIR", RUNTIME_ROOT / "data")
RESULTS_DIR = _configured_path("SIMULATOR_RESULTS_DIR", RUNTIME_ROOT / "results")
_source_assets_dir = CORPUS_DIR / "assets"
ASSETS_DIR = _configured_path(
    "SIMULATOR_CORPUS_ASSETS_DIR",
    _source_assets_dir if _source_checkout else DATA_DIR / "corpus-assets",
)
CORPUS_MANIFEST = _configured_path(
    "SIMULATOR_CORPUS_MANIFEST",
    (
        CORPUS_DIR / "assets_manifest.json"
        if ASSETS_DIR == _source_assets_dir
        else ASSETS_DIR.parent / "assets_manifest.json"
    ),
)
COMPILED_ASSETS_DIR = _configured_path(
    "SIMULATOR_COMPILED_ASSETS_DIR",
    DATA_DIR / "compiled-assets",
)
