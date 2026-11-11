"""SQLite connection lifecycle, schema migrations and repository composition."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..paths import DATA_DIR
from .calls import CallsStoreMixin
from .common import StoreConflictError
from .numbers import NumbersStoreMixin
from .operations import OperationsStoreMixin
from .queues import QueuesStoreMixin
from .resources import ResourcesStoreMixin
from .runs import RunsStoreMixin


class SQLiteSimulatorStore(
    ResourcesStoreMixin,
    NumbersStoreMixin,
    QueuesStoreMixin,
    OperationsStoreMixin,
    RunsStoreMixin,
    CallsStoreMixin,
):
    # MySQL overrides the locking suffix used by queue claims and cancellation.
    _LOCKING_READ = ""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get("SIMULATOR_DB_PATH")
        self.path = Path(configured) if configured else DATA_DIR / "telephony_voice_simulator.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Keep an anchor connection for this repository's private shared-memory
        # database. Individual transactions still get independent connections.
        self._database = (
            f"file:simulator_{uuid.uuid4().hex}?mode=memory&cache=shared"
            if str(self.path) == ":memory:"
            else str(self.path)
        )
        self._memory_anchor = (
            sqlite3.connect(self._database, uri=True)
            if str(self.path) == ":memory:"
            else None
        )
        self._closed = False
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._closed:
            raise RuntimeError("The simulator repository is closed")
        connection = sqlite3.connect(self._database, uri=self._memory_anchor is not None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError("Database uniqueness or relationship conflict") from exc
        finally:
            connection.close()

    def close(self) -> None:
        """Release an in-memory repository; file-backed connections close per operation."""

        if getattr(self, "_memory_anchor", None) is not None:
            self._memory_anchor.close()
            self._memory_anchor = None
        self._closed = True

    def _migrate(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_connections (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    description TEXT NOT NULL DEFAULT '',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS endpoints (
                    id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL REFERENCES provider_connections(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    address TEXT NOT NULL,
                    routing_mode TEXT NOT NULL DEFAULT 'fixed',
                    default_scenario TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(connection_id, address)
                );
                CREATE TABLE IF NOT EXISTS provider_numbers (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    connection_id TEXT NOT NULL
                        REFERENCES provider_connections(id) ON DELETE CASCADE,
                    endpoint_id TEXT REFERENCES endpoints(id) ON DELETE SET NULL,
                    provider_resource_id TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    friendly_name TEXT NOT NULL,
                    voice_url TEXT NOT NULL DEFAULT '',
                    voice_method TEXT NOT NULL DEFAULT 'POST',
                    status TEXT NOT NULL DEFAULT 'active',
                    managed_mode TEXT NOT NULL DEFAULT 'amd',
                    capabilities_json TEXT NOT NULL DEFAULT '{}',
                    configuration_json TEXT NOT NULL DEFAULT '{}',
                    last_synced_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_resource_id),
                    UNIQUE(provider, phone_number)
                );
                CREATE INDEX IF NOT EXISTS provider_numbers_connection
                    ON provider_numbers(connection_id);
                CREATE TABLE IF NOT EXISTS directory_entries (
                    id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL
                        REFERENCES provider_connections(id) ON DELETE CASCADE,
                    extension TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    department TEXT NOT NULL DEFAULT 'General',
                    ring_timeout INTEGER NOT NULL DEFAULT 25,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS simulation_runs (
                    id TEXT PRIMARY KEY,
                    endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    status TEXT NOT NULL,
                    caller_number TEXT,
                    extension TEXT,
                    destination TEXT,
                    outcome TEXT,
                    duration_seconds INTEGER,
                    timeline_json TEXT NOT NULL DEFAULT '[]',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS simulation_runs_created_at
                    ON simulation_runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS incoming_calls (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'inbound',
                    from_address TEXT NOT NULL DEFAULT '',
                    to_address TEXT NOT NULL DEFAULT '',
                    scenario TEXT,
                    status TEXT NOT NULL,
                    recording_status TEXT NOT NULL DEFAULT 'not_started',
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_s REAL,
                    analysis_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS incoming_calls_started_at
                    ON incoming_calls(started_at DESC);
                CREATE TABLE IF NOT EXISTS call_recordings (
                    id TEXT PRIMARY KEY,
                    call_id TEXT NOT NULL REFERENCES incoming_calls(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_s REAL,
                    channels INTEGER,
                    provider_url TEXT,
                    local_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS call_recordings_call_id
                    ON call_recordings(call_id);
                CREATE TABLE IF NOT EXISTS deleted_incoming_calls (
                    id TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS managed_provider_addresses (
                    provider TEXT NOT NULL,
                    address TEXT NOT NULL,
                    first_managed_at TEXT NOT NULL,
                    PRIMARY KEY (provider, address)
                );
                CREATE TABLE IF NOT EXISTS telephony_run_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    address TEXT NOT NULL,
                    scenario TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS telephony_run_queue_provider_address
                    ON telephony_run_queue(provider, address, id);
                CREATE TABLE IF NOT EXISTS customer_persona_queue (
                    id TEXT PRIMARY KEY,
                    number TEXT NOT NULL,
                    persona_json TEXT NOT NULL,
                    label TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS customer_persona_queue_number
                    ON customer_persona_queue(number, created_at);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    error TEXT,
                    log_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS operations_created ON operations(created_at);
                """
            )
            self._ensure_column(db, "provider_connections", "status", "TEXT NOT NULL DEFAULT 'ready'")
            self._ensure_column(
                db, "provider_connections", "description", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(db, "simulation_runs", "caller_number", "TEXT")
            self._ensure_column(db, "simulation_runs", "extension", "TEXT")
            self._ensure_column(db, "simulation_runs", "destination", "TEXT")
            self._ensure_column(db, "simulation_runs", "outcome", "TEXT")
            self._ensure_column(db, "simulation_runs", "duration_seconds", "INTEGER")
            # Ownership is intentionally append-only. Removing a local
            # endpoint does not detach the Twilio number at the carrier, so a
            # deleted row must never make that address look unmanaged and
            # silently fall back to legacy/public routing.
            db.execute(
                """
                INSERT OR IGNORE INTO managed_provider_addresses (
                    provider, address, first_managed_at
                )
                SELECT provider_connections.provider, endpoints.address, endpoints.created_at
                FROM endpoints
                JOIN provider_connections
                  ON provider_connections.id = endpoints.connection_id
                """
            )
            # One-time compatibility migration from the former Twilio-only
            # ownership and queue tables. Drop them after copying so the
            # provider-neutral schema remains the sole source of truth.
            if self._table_exists(db, "managed_twilio_addresses"):
                db.execute(
                    """
                    INSERT OR IGNORE INTO managed_provider_addresses (
                        provider, address, first_managed_at
                    )
                    SELECT 'twilio', address, first_managed_at
                    FROM managed_twilio_addresses
                    """
                )
            if self._table_exists(db, "pstn_run_queue"):
                db.execute(
                    """
                    INSERT INTO telephony_run_queue (
                        provider, address, scenario, run_id, created_at
                    )
                    SELECT 'twilio', address, scenario, run_id, created_at
                    FROM pstn_run_queue
                    """
                )
            db.execute("DROP TRIGGER IF EXISTS remember_twilio_endpoint_insert")
            db.execute("DROP TRIGGER IF EXISTS remember_twilio_endpoint_update")
            db.execute("DROP TABLE IF EXISTS pstn_run_queue")
            db.execute("DROP TABLE IF EXISTS managed_twilio_addresses")
            db.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS remember_provider_endpoint_insert
                AFTER INSERT ON endpoints
                BEGIN
                    INSERT OR IGNORE INTO managed_provider_addresses (
                        provider, address, first_managed_at
                    )
                    SELECT provider, NEW.address, NEW.created_at
                    FROM provider_connections
                    WHERE id = NEW.connection_id;
                END;
                CREATE TRIGGER IF NOT EXISTS remember_provider_endpoint_update
                AFTER UPDATE OF address, connection_id ON endpoints
                BEGIN
                    INSERT OR IGNORE INTO managed_provider_addresses (
                        provider, address, first_managed_at
                    )
                    SELECT provider, OLD.address, OLD.created_at
                    FROM provider_connections
                    WHERE id = OLD.connection_id;
                    INSERT OR IGNORE INTO managed_provider_addresses (
                        provider, address, first_managed_at
                    )
                    SELECT provider, NEW.address, NEW.created_at
                    FROM provider_connections
                    WHERE id = NEW.connection_id;
                END;
                """
            )

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _ensure_column(
        db: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        """Apply a small additive migration without replacing local data."""
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class SimulatorStore:
    """Backward-compatible repository factory.

    Existing callers can keep using ``SimulatorStore(path)``. New deployments
    can pass ``uri=...`` or set ``SIMULATOR_DATABASE_URI``.
    """

    def __new__(
        cls,
        path: str | Path | None = None,
        *,
        uri: str | None = None,
    ):
        from .database import create_simulator_store

        return create_simulator_store(path, uri=uri)
