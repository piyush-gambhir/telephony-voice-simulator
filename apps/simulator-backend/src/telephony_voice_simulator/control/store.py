"""Small SQLite repository used by the local control plane."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..paths import DATA_DIR
from .models import (
    CallRecording,
    DirectoryEntry,
    Endpoint,
    IncomingCall,
    ProviderConnection,
    ProviderNumber,
    SimulationRun,
)


class StoreConflictError(RuntimeError):
    """A repository write conflicts with an existing record."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SQLiteSimulatorStore:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.environ.get("SIMULATOR_DB_PATH")
        self.path = Path(configured) if configured else DATA_DIR / "telephony_voice_simulator.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

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

    @staticmethod
    def _connection(row: sqlite3.Row) -> ProviderConnection:
        return ProviderConnection(
            id=row["id"],
            provider=row["provider"],
            name=row["name"],
            status=row["status"],
            description=row["description"],
            settings=json.loads(row["settings_json"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _endpoint(row: sqlite3.Row) -> Endpoint:
        return Endpoint(
            id=row["id"],
            connection_id=row["connection_id"],
            name=row["name"],
            kind=row["kind"],
            address=row["address"],
            routing_mode=row["routing_mode"],
            default_scenario=row["default_scenario"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _provider_number(row: sqlite3.Row) -> ProviderNumber:
        return ProviderNumber(
            id=row["id"],
            provider=row["provider"],
            connection_id=row["connection_id"],
            endpoint_id=row["endpoint_id"],
            provider_resource_id=row["provider_resource_id"],
            phone_number=row["phone_number"],
            friendly_name=row["friendly_name"],
            voice_url=row["voice_url"],
            voice_method=row["voice_method"],
            status=row["status"],
            managed_mode=row["managed_mode"],
            capabilities=json.loads(row["capabilities_json"]),
            configuration=json.loads(row["configuration_json"]),
            last_synced_at=row["last_synced_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> SimulationRun:
        return SimulationRun(
            id=row["id"],
            endpoint_id=row["endpoint_id"],
            provider=row["provider"],
            scenario=row["scenario"],
            status=row["status"],
            caller_number=row["caller_number"],
            extension=row["extension"],
            destination=row["destination"],
            outcome=row["outcome"],
            duration_seconds=row["duration_seconds"],
            timeline=json.loads(row["timeline_json"]),
            result=json.loads(row["result_json"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    def list_connections(self) -> list[ProviderConnection]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM provider_connections ORDER BY created_at").fetchall()
        return [self._connection(row) for row in rows]

    def get_connection(self, connection_id: str) -> ProviderConnection | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM provider_connections WHERE id = ?", (connection_id,)
            ).fetchone()
        return self._connection(row) if row else None

    def create_connection(
        self,
        provider: str,
        name: str,
        settings: dict[str, Any] | None = None,
        *,
        status: str = "ready",
        description: str = "",
    ) -> ProviderConnection:
        record = ProviderConnection(
            id=_id("provider"),
            provider=provider,
            name=name,
            status=status,
            description=description,
            settings=settings or {},
            enabled=status != "disabled",
            created_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO provider_connections (
                    id, provider, name, status, description, settings_json, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.provider,
                    record.name,
                    record.status,
                    record.description,
                    json.dumps(record.settings),
                    int(record.enabled),
                    record.created_at,
                ),
            )
        return record

    def update_connection(
        self,
        connection_id: str,
        *,
        name: str,
        status: str,
        description: str,
        settings: dict[str, Any],
    ) -> ProviderConnection | None:
        current = self.get_connection(connection_id)
        if current is None:
            return None
        updated = replace(
            current,
            name=name,
            status=status,
            description=description,
            settings=settings,
            enabled=status != "disabled",
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE provider_connections
                SET name = ?, status = ?, description = ?, settings_json = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    updated.name,
                    updated.status,
                    updated.description,
                    json.dumps(updated.settings),
                    int(updated.enabled),
                    updated.id,
                ),
            )
        return updated

    def delete_connection(self, connection_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM provider_connections WHERE id = ?", (connection_id,))
        return cursor.rowcount > 0

    def list_endpoints(self) -> list[Endpoint]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM endpoints ORDER BY created_at").fetchall()
        return [self._endpoint(row) for row in rows]

    def get_endpoint(self, endpoint_id: str) -> Endpoint | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
        return self._endpoint(row) if row else None

    def create_endpoint(
        self,
        *,
        connection_id: str,
        name: str,
        kind: str,
        address: str,
        routing_mode: str = "fixed",
        default_scenario: str | None = None,
    ) -> Endpoint:
        record = Endpoint(
            id=_id("endpoint"),
            connection_id=connection_id,
            name=name,
            kind=kind,
            address=address,
            routing_mode=routing_mode,
            default_scenario=default_scenario,
            created_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO endpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.connection_id,
                    record.name,
                    record.kind,
                    record.address,
                    record.routing_mode,
                    record.default_scenario,
                    int(record.enabled),
                    record.created_at,
                ),
            )
        return record

    def update_endpoint(
        self,
        endpoint_id: str,
        *,
        connection_id: str,
        name: str,
        kind: str,
        address: str,
        routing_mode: str,
        default_scenario: str | None,
        enabled: bool,
    ) -> Endpoint | None:
        current = self.get_endpoint(endpoint_id)
        if current is None:
            return None
        updated = replace(
            current,
            connection_id=connection_id,
            name=name,
            kind=kind,
            address=address,
            routing_mode=routing_mode,
            default_scenario=default_scenario,
            enabled=enabled,
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE endpoints
                SET connection_id = ?, name = ?, kind = ?, address = ?,
                    routing_mode = ?, default_scenario = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    updated.connection_id,
                    updated.name,
                    updated.kind,
                    updated.address,
                    updated.routing_mode,
                    updated.default_scenario,
                    int(updated.enabled),
                    updated.id,
                ),
            )
        return updated

    def delete_endpoint(self, endpoint_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM endpoints WHERE id = ?", (endpoint_id,))
        return cursor.rowcount > 0

    def list_provider_numbers(
        self,
        *,
        provider: str | None = None,
        managed_mode: str | None = None,
    ) -> list[ProviderNumber]:
        clauses: list[str] = []
        parameters: list[object] = []
        if provider is not None:
            clauses.append("provider = ?")
            parameters.append(provider)
        if managed_mode is not None:
            clauses.append("managed_mode = ?")
            parameters.append(managed_mode)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM provider_numbers{where} ORDER BY phone_number",
                tuple(parameters),
            ).fetchall()
        return [self._provider_number(row) for row in rows]

    def get_provider_number(self, number_id: str) -> ProviderNumber | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM provider_numbers WHERE id = ?",
                (number_id,),
            ).fetchone()
        return self._provider_number(row) if row else None

    def upsert_provider_number(
        self,
        *,
        provider: str,
        connection_id: str,
        endpoint_id: str | None,
        provider_resource_id: str,
        phone_number: str,
        friendly_name: str,
        voice_url: str,
        voice_method: str = "POST",
        status: str = "active",
        managed_mode: str = "amd",
        capabilities: dict[str, Any] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ProviderNumber:
        now = _now()
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM provider_numbers
                WHERE provider = ? AND provider_resource_id = ?
                """,
                (provider, provider_resource_id),
            ).fetchone()
            if row is None:
                record = ProviderNumber(
                    id=_id("number"),
                    provider=provider,
                    connection_id=connection_id,
                    endpoint_id=endpoint_id,
                    provider_resource_id=provider_resource_id,
                    phone_number=phone_number,
                    friendly_name=friendly_name,
                    voice_url=voice_url,
                    voice_method=voice_method,
                    status=status,
                    managed_mode=managed_mode,
                    capabilities=capabilities or {},
                    configuration=configuration or {},
                    last_synced_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.execute(
                    """
                    INSERT INTO provider_numbers (
                        id, provider, connection_id, endpoint_id, provider_resource_id,
                        phone_number, friendly_name, voice_url, voice_method, status,
                        managed_mode, capabilities_json, configuration_json,
                        last_synced_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.provider,
                        record.connection_id,
                        record.endpoint_id,
                        record.provider_resource_id,
                        record.phone_number,
                        record.friendly_name,
                        record.voice_url,
                        record.voice_method,
                        record.status,
                        record.managed_mode,
                        json.dumps(record.capabilities),
                        json.dumps(record.configuration),
                        record.last_synced_at,
                        record.created_at,
                        record.updated_at,
                    ),
                )
                return record
            existing = self._provider_number(row)
            merged_configuration = (
                existing.configuration if configuration is None else configuration
            )
            merged_capabilities = existing.capabilities if capabilities is None else capabilities
            db.execute(
                """
                UPDATE provider_numbers
                SET connection_id = ?, endpoint_id = ?, phone_number = ?,
                    friendly_name = ?, voice_url = ?, voice_method = ?, status = ?,
                    managed_mode = ?, capabilities_json = ?, configuration_json = ?,
                    last_synced_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    connection_id,
                    endpoint_id,
                    phone_number,
                    friendly_name,
                    voice_url,
                    voice_method,
                    status,
                    managed_mode,
                    json.dumps(merged_capabilities),
                    json.dumps(merged_configuration),
                    now,
                    now,
                    existing.id,
                ),
            )
        return replace(
            existing,
            connection_id=connection_id,
            endpoint_id=endpoint_id,
            phone_number=phone_number,
            friendly_name=friendly_name,
            voice_url=voice_url,
            voice_method=voice_method,
            status=status,
            managed_mode=managed_mode,
            capabilities=merged_capabilities,
            configuration=merged_configuration,
            last_synced_at=now,
            updated_at=now,
        )

    def update_provider_number(
        self,
        number_id: str,
        *,
        friendly_name: str | None = None,
        voice_url: str | None = None,
        voice_method: str | None = None,
        status: str | None = None,
        endpoint_id: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ProviderNumber | None:
        current = self.get_provider_number(number_id)
        if current is None:
            return None
        updated = replace(
            current,
            friendly_name=current.friendly_name if friendly_name is None else friendly_name,
            voice_url=current.voice_url if voice_url is None else voice_url,
            voice_method=current.voice_method if voice_method is None else voice_method,
            status=current.status if status is None else status,
            endpoint_id=current.endpoint_id if endpoint_id is None else endpoint_id,
            configuration=(
                current.configuration if configuration is None else configuration
            ),
            updated_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE provider_numbers
                SET friendly_name = ?, voice_url = ?, voice_method = ?, status = ?,
                    endpoint_id = ?, configuration_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    updated.friendly_name,
                    updated.voice_url,
                    updated.voice_method,
                    updated.status,
                    updated.endpoint_id,
                    json.dumps(updated.configuration),
                    updated.updated_at,
                    updated.id,
                ),
            )
        return updated

    def list_managed_provider_addresses(self, provider: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT address
                FROM managed_provider_addresses
                WHERE provider = ?
                ORDER BY first_managed_at
                """,
                (provider,),
            ).fetchall()
        return [str(row["address"]) for row in rows]

    def remember_provider_address(self, provider: str, address: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO managed_provider_addresses (
                    provider, address, first_managed_at
                ) VALUES (?, ?, ?)
                """,
                (provider, address, _now()),
            )

    def enqueue_provider_run(
        self,
        *,
        provider: str,
        address: str,
        scenario: str,
        run_id: str | None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO telephony_run_queue (
                    provider, address, scenario, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (provider, address, scenario, run_id, _now()),
            )

    def dequeue_provider_run(
        self,
        provider: str,
        address: str,
    ) -> dict[str, str | None] | None:
        """Atomically consume the oldest persisted provider assignment."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT id, scenario, run_id
                FROM telephony_run_queue
                WHERE provider = ? AND address = ?
                ORDER BY id
                LIMIT 1
                """,
                (provider, address),
            ).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM telephony_run_queue WHERE id = ?", (row["id"],))
        return {"scenario": str(row["scenario"]), "run_id": row["run_id"]}

    def list_provider_queue(
        self,
        provider: str,
        address: str,
    ) -> list[dict[str, str | None]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT scenario, run_id
                FROM telephony_run_queue
                WHERE provider = ? AND address = ?
                ORDER BY id
                """,
                (provider, address),
            ).fetchall()
        return [
            {"scenario": str(row["scenario"]), "run_id": row["run_id"]}
            for row in rows
        ]

    def fail_provider_queue(self, provider: str, address: str, reason: str) -> int:
        """Fail linked runs and remove stale assignments for one provider address."""

        timestamp = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT DISTINCT queue.run_id, runs.result_json
                FROM telephony_run_queue AS queue
                LEFT JOIN simulation_runs AS runs ON runs.id = queue.run_id
                WHERE queue.provider = ? AND queue.address = ?
                """,
                (provider, address),
            ).fetchall()
            failed = 0
            for row in rows:
                if row["run_id"] is None or row["result_json"] is None:
                    continue
                result = json.loads(row["result_json"])
                result.update({"graded": False, "error": reason})
                cursor = db.execute(
                    """
                    UPDATE simulation_runs
                    SET status = 'failed', result_json = ?, completed_at = ?
                    WHERE id = ? AND completed_at IS NULL
                    """,
                    (json.dumps(result), timestamp, row["run_id"]),
                )
                failed += cursor.rowcount
            db.execute(
                "DELETE FROM telephony_run_queue WHERE provider = ? AND address = ?",
                (provider, address),
            )
        return failed

    # Compatibility aliases retained for existing PSTN scripts and downstream
    # users. New provider modules only consume the neutral methods above.
    def list_managed_twilio_addresses(self) -> list[str]:
        return self.list_managed_provider_addresses("twilio")

    def enqueue_pstn_run(
        self,
        *,
        address: str,
        scenario: str,
        run_id: str | None,
    ) -> None:
        self.enqueue_provider_run(
            provider="twilio",
            address=address,
            scenario=scenario,
            run_id=run_id,
        )

    def dequeue_pstn_run(self, address: str) -> dict[str, str | None] | None:
        return self.dequeue_provider_run("twilio", address)

    def list_pstn_queue(self, address: str) -> list[dict[str, str | None]]:
        return self.list_provider_queue("twilio", address)

    def fail_pstn_queue(self, address: str, reason: str) -> int:
        return self.fail_provider_queue("twilio", address, reason)

    @staticmethod
    def _directory_entry(row: sqlite3.Row) -> DirectoryEntry:
        return DirectoryEntry(
            id=row["id"],
            connection_id=row["connection_id"],
            extension=row["extension"],
            name=row["name"],
            destination=row["destination"],
            department=row["department"],
            ring_timeout=row["ring_timeout"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
        )

    def list_directory_entries(self) -> list[DirectoryEntry]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM directory_entries ORDER BY extension"
            ).fetchall()
        return [self._directory_entry(row) for row in rows]

    def get_directory_entry_by_extension(self, extension: str) -> DirectoryEntry | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM directory_entries WHERE extension = ?", (extension,)
            ).fetchone()
        return self._directory_entry(row) if row else None

    def get_directory_entry(self, entry_id: str) -> DirectoryEntry | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM directory_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return self._directory_entry(row) if row else None

    def create_directory_entry(
        self,
        *,
        connection_id: str,
        extension: str,
        name: str,
        destination: str,
        department: str,
        ring_timeout: int,
    ) -> DirectoryEntry:
        record = DirectoryEntry(
            id=_id("directory"),
            connection_id=connection_id,
            extension=extension,
            name=name,
            destination=destination,
            department=department,
            ring_timeout=ring_timeout,
            created_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO directory_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.connection_id,
                    record.extension,
                    record.name,
                    record.destination,
                    record.department,
                    record.ring_timeout,
                    int(record.enabled),
                    record.created_at,
                ),
            )
        return record

    def update_directory_entry(
        self,
        entry_id: str,
        *,
        connection_id: str,
        extension: str,
        name: str,
        destination: str,
        department: str,
        ring_timeout: int,
        enabled: bool,
    ) -> DirectoryEntry | None:
        current = self.get_directory_entry(entry_id)
        if current is None:
            return None
        updated = replace(
            current,
            connection_id=connection_id,
            extension=extension,
            name=name,
            destination=destination,
            department=department,
            ring_timeout=ring_timeout,
            enabled=enabled,
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE directory_entries
                SET connection_id = ?, extension = ?, name = ?, destination = ?,
                    department = ?, ring_timeout = ?, enabled = ?
                WHERE id = ?
                """,
                (
                    updated.connection_id,
                    updated.extension,
                    updated.name,
                    updated.destination,
                    updated.department,
                    updated.ring_timeout,
                    int(updated.enabled),
                    updated.id,
                ),
            )
        return updated

    def delete_directory_entry(self, entry_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM directory_entries WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    def list_runs(self, limit: int = 100) -> list[SimulationRun]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM simulation_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run(row) for row in rows]

    def get_run(self, run_id: str) -> SimulationRun | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM simulation_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def create_run(
        self,
        endpoint_id: str,
        provider: str,
        scenario: str,
        *,
        caller_number: str | None = None,
        extension: str | None = None,
        destination: str | None = None,
        outcome: str | None = None,
        duration_seconds: int | None = None,
    ) -> SimulationRun:
        record = SimulationRun(
            id=_id("run"),
            endpoint_id=endpoint_id,
            provider=provider,
            scenario=scenario,
            status="created",
            caller_number=caller_number,
            extension=extension,
            destination=destination,
            outcome=outcome,
            duration_seconds=duration_seconds,
            created_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO simulation_runs (
                    id, endpoint_id, provider, scenario, status, caller_number,
                    extension, destination, outcome, duration_seconds,
                    timeline_json, result_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.endpoint_id,
                    record.provider,
                    record.scenario,
                    record.status,
                    record.caller_number,
                    record.extension,
                    record.destination,
                    record.outcome,
                    record.duration_seconds,
                    "[]",
                    "{}",
                    record.created_at,
                    None,
                ),
            )
        return record

    def update_run(
        self,
        run: SimulationRun,
        *,
        status: str,
        timeline: list[dict[str, Any]] | None = None,
        result: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> SimulationRun:
        updated = replace(
            run,
            status=status,
            timeline=timeline if timeline is not None else run.timeline,
            result=result if result is not None else run.result,
            completed_at=_now() if completed else run.completed_at,
        )
        with self._connect() as db:
            db.execute(
                """UPDATE simulation_runs
                   SET status = ?, timeline_json = ?, result_json = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    updated.status,
                    json.dumps(updated.timeline),
                    json.dumps(updated.result),
                    updated.completed_at,
                    updated.id,
                ),
            )
        return updated

    @staticmethod
    def _recording(row: sqlite3.Row) -> CallRecording:
        return CallRecording(
            id=row["id"],
            call_id=row["call_id"],
            provider=row["provider"],
            kind=row["kind"],
            status=row["status"],
            duration_s=row["duration_s"],
            channels=row["channels"],
            provider_url=row["provider_url"],
            local_path=row["local_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _recordings_for(self, db: sqlite3.Connection, call_id: str) -> list[CallRecording]:
        rows = db.execute(
            "SELECT * FROM call_recordings WHERE call_id = ? ORDER BY created_at", (call_id,)
        ).fetchall()
        return [self._recording(row) for row in rows]

    def _call(self, db: sqlite3.Connection, row: sqlite3.Row) -> IncomingCall:
        return IncomingCall(
            id=row["id"],
            provider=row["provider"],
            direction=row["direction"],
            from_address=row["from_address"],
            to_address=row["to_address"],
            scenario=row["scenario"],
            status=row["status"],
            recording_status=row["recording_status"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_s=row["duration_s"],
            analysis=json.loads(row["analysis_json"]),
            recordings=self._recordings_for(db, row["id"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_incoming_call(
        self,
        *,
        call_id: str,
        provider: str = "twilio",
        from_address: str = "",
        to_address: str = "",
        scenario: str | None = None,
        status: str = "in-progress",
        recording_status: str = "not_started",
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_s: float | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> IncomingCall | None:
        timestamp = _now()
        started = started_at or timestamp
        with self._connect() as db:
            # Serialize the tombstone check with all call writes. This prevents
            # a late callback racing a deletion in another server process.
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM deleted_incoming_calls WHERE id = ?", (call_id,)
            ).fetchone():
                return None
            db.execute(
                """
                INSERT INTO incoming_calls (
                    id, provider, direction, from_address, to_address, scenario,
                    status, recording_status, started_at, ended_at, duration_s,
                    analysis_json, created_at, updated_at
                ) VALUES (?, ?, 'inbound', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    from_address = CASE WHEN excluded.from_address = '' THEN from_address
                                        ELSE excluded.from_address END,
                    to_address = CASE WHEN excluded.to_address = '' THEN to_address
                                      ELSE excluded.to_address END,
                    scenario = COALESCE(excluded.scenario, scenario),
                    status = excluded.status,
                    recording_status = CASE
                        WHEN excluded.recording_status = 'not_started' THEN recording_status
                        ELSE excluded.recording_status END,
                    ended_at = COALESCE(excluded.ended_at, ended_at),
                    duration_s = COALESCE(excluded.duration_s, duration_s),
                    analysis_json = CASE WHEN excluded.analysis_json = '{}'
                                         THEN analysis_json ELSE excluded.analysis_json END,
                    updated_at = excluded.updated_at
                """,
                (
                    call_id,
                    provider,
                    from_address,
                    to_address,
                    scenario,
                    status,
                    recording_status,
                    started,
                    ended_at,
                    duration_s,
                    json.dumps(analysis or {}),
                    timestamp,
                    timestamp,
                ),
            )
            row = db.execute("SELECT * FROM incoming_calls WHERE id = ?", (call_id,)).fetchone()
            assert row is not None
            return self._call(db, row)

    def upsert_recording(
        self,
        *,
        recording_id: str,
        call_id: str,
        provider: str,
        kind: str,
        status: str,
        duration_s: float | None = None,
        channels: int | None = None,
        provider_url: str | None = None,
        local_path: str | None = None,
    ) -> CallRecording | None:
        timestamp = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM deleted_incoming_calls WHERE id = ?", (call_id,)
            ).fetchone():
                return None
            db.execute(
                """
                INSERT INTO call_recordings (
                    id, call_id, provider, kind, status, duration_s, channels,
                    provider_url, local_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    status = excluded.status,
                    duration_s = COALESCE(excluded.duration_s, duration_s),
                    channels = COALESCE(excluded.channels, channels),
                    provider_url = COALESCE(excluded.provider_url, provider_url),
                    local_path = COALESCE(excluded.local_path, local_path),
                    updated_at = excluded.updated_at
                """,
                (
                    recording_id,
                    call_id,
                    provider,
                    kind,
                    status,
                    duration_s,
                    channels,
                    provider_url,
                    local_path,
                    timestamp,
                    timestamp,
                ),
            )
            row = db.execute(
                "SELECT * FROM call_recordings WHERE id = ?", (recording_id,)
            ).fetchone()
            assert row is not None
            return self._recording(row)

    def list_incoming_calls(self, limit: int = 100) -> list[IncomingCall]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM incoming_calls ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._call(db, row) for row in rows]

    def get_incoming_call(self, call_id: str) -> IncomingCall | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM incoming_calls WHERE id = ?", (call_id,)).fetchone()
            return self._call(db, row) if row else None

    def is_incoming_call_deleted(self, call_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM deleted_incoming_calls WHERE id = ?", (call_id,)
            ).fetchone()
        return row is not None

    def tombstone_incoming_call(self, call_id: str) -> None:
        """Persist a deletion marker for a process-local call without a row."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO deleted_incoming_calls (id, deleted_at)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET deleted_at = excluded.deleted_at
                """,
                (call_id, _now()),
            )

    def delete_incoming_call(self, call_id: str) -> bool:
        """Atomically tombstone a call and delete it with its recording rows."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM incoming_calls WHERE id = ?", (call_id,)
            ).fetchone():
                return False
            db.execute(
                """
                INSERT INTO deleted_incoming_calls (id, deleted_at)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET deleted_at = excluded.deleted_at
                """,
                (call_id, _now()),
            )
            cursor = db.execute("DELETE FROM incoming_calls WHERE id = ?", (call_id,))
        return cursor.rowcount > 0

    def get_recording(self, recording_id: str) -> CallRecording | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM call_recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        return self._recording(row) if row else None


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
