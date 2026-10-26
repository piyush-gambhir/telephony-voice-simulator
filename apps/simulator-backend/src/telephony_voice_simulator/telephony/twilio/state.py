"""Process-local Twilio runtime state: live calls, the store handle and call tombstones."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ...paths import ASSETS_DIR, COMPILED_ASSETS_DIR, RESULTS_DIR, SCENARIOS_DIR
from ...storage.sqlite import SimulatorStore
from .compile import (
    CompiledScenario,
    compile_scenario,
)

COMPILED_DIR = COMPILED_ASSETS_DIR
PSTN_RESULTS_DIR = RESULTS_DIR / "pstn"


class SimState:
    def __init__(self) -> None:
        self.scenarios: dict[str, CompiledScenario] = {}
        self.raw_scenarios: dict[str, dict] = {}
        self.compiled_assets: set[str] = set()
        # Answers numbers that no endpoint manages.
        self.default_scenario = os.environ.get("PSTN_DEFAULT_SCENARIO", "")
        self.calls: dict[str, dict] = {}  # CallSid -> record
        self.recording_tasks: dict[str, dict[str, asyncio.Task]] = {}
        self.finalization_tasks: dict[str, asyncio.Task] = {}
        # A deleted Twilio CallSid must not be resurrected by a late recording
        # or status callback. Twilio CallSids are globally unique.
        self.deleted_call_ids: set[str] = set()

    def load_scenarios(self) -> None:
        COMPILED_DIR.mkdir(parents=True, exist_ok=True)
        for stale in COMPILED_DIR.glob("*.wav"):
            if stale.is_file() and not stale.is_symlink():
                stale.unlink()
        self.scenarios.clear()
        self.raw_scenarios.clear()
        self.compiled_assets.clear()
        missing_assets: list[str] = []
        for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text())
            try:
                compiled = compile_scenario(raw, assets_dir=ASSETS_DIR, out_dir=COMPILED_DIR)
            except FileNotFoundError as exc:
                missing = Path(exc.filename).name if exc.filename else str(exc)
                missing_assets.append(f"{raw.get('name')}: {missing}")
                continue
            self.scenarios[compiled.name] = compiled
            self.raw_scenarios[compiled.name] = raw
            self.compiled_assets.update(
                segment.audio_file
                for sequence in compiled.sequences.values()
                for segment in sequence
                if segment.audio_file
            )
        if missing_assets:
            self.scenarios.clear()
            self.raw_scenarios.clear()
            self.compiled_assets.clear()
            preview = ", ".join(missing_assets[:5])
            extra = len(missing_assets) - 5
            suffix = f" (+{extra} more)" if extra > 0 else ""
            raise SystemExit(
                "AMD PSTN corpus is incomplete. Mount a rights-reviewed private corpus and set "
                "SIMULATOR_CORPUS_ASSETS_DIR, or start IVR-only mode with --with-ivr. "
                f"Missing: {preview}{suffix}"
            )
        if not self.scenarios:
            raise SystemExit(
                "No AMD scenarios compiled. Set SIMULATOR_CORPUS_ASSETS_DIR to a "
                "rights-reviewed private corpus, or use --with-ivr."
            )
STATE = SimState()
CALL_STORE: SimulatorStore | None = None


def call_store() -> SimulatorStore:
    global CALL_STORE
    if CALL_STORE is None:
        CALL_STORE = SimulatorStore()
    return CALL_STORE


def evict_call(call_id: str) -> None:
    """Remove process-local call state and reject late callbacks for this SID."""

    STATE.deleted_call_ids.add(call_id)
    STATE.calls.pop(call_id, None)
    pending = [*STATE.recording_tasks.pop(call_id, {}).values()]
    if task := STATE.finalization_tasks.pop(call_id, None):
        pending.append(task)
    for task in pending:
        task.cancel()
    from .customer import evict_call as evict_customer_call

    evict_customer_call(call_id)


def call_is_deleted(call_id: str) -> bool:
    """Consult both the process cache and persistent privacy tombstones."""

    if call_id in STATE.deleted_call_ids:
        return True
    if call_store().is_incoming_call_deleted(call_id):
        evict_call(call_id)
        return True
    return False


def iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time(), UTC).isoformat()


def epoch(value: str | None, default: float | None = None) -> float:
    if value:
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(value).timestamp()
    return time.time() if default is None else default


def optional_float(raw: object) -> float | None:
    try:
        return float(str(raw)) if str(raw) else None
    except ValueError:
        return None
