"""MySQL implementation of the simulator repository.

The application repository deliberately uses a small SQL surface. This adapter
keeps the domain/service layers database-agnostic while translating the qmark
parameters and upsert syntax used by the SQLite implementation.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .database import parse_mysql_uri
from .models import CallRecording, IncomingCall, SimulationRun
from .store import SQLiteSimulatorStore, StoreConflictError

_Result = TypeVar("_Result")


def _mysql_sql(statement: str) -> str:
    sql = statement.replace("INSERT OR IGNORE", "INSERT IGNORE")
    sql = sql.replace("ON CONFLICT(id) DO UPDATE SET", "ON DUPLICATE KEY UPDATE")
    sql = re.sub(r"\bexcluded\.([a-z_]+)", r"VALUES(\1)", sql)
    return sql.replace("?", "%s")


class _MySQLConnection:
    def __init__(self, raw: Any, integrity_error: type[Exception]) -> None:
        self.raw = raw
        self.integrity_error = integrity_error
        self.cursor = raw.cursor()

    def __enter__(self) -> _MySQLConnection:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self.raw.commit()
            else:
                self.raw.rollback()
        finally:
            self.cursor.close()
            self.raw.close()

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> Any:
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            # Tombstone locking reads must protect an absent row's gap as well
            # as an existing marker. READ COMMITTED disables that protection;
            # set the isolation level for this transaction before BEGIN.
            self.cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.raw.begin()
            return self.cursor
        try:
            self.cursor.execute(_mysql_sql(statement), parameters)
        except self.integrity_error as exc:
            raise StoreConflictError("Database uniqueness or relationship conflict") from exc
        return self.cursor


class MySQLSimulatorStore(SQLiteSimulatorStore):
    """Persistent repository selected by a ``mysql://`` database URI."""

    def __init__(self, uri: str) -> None:
        self.settings = parse_mysql_uri(uri)
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - dependency is packaged
            raise RuntimeError(
                "MySQL support requires PyMySQL. Install backend dependencies again."
            ) from exc
        self._pymysql = pymysql
        self._migrate()

    def _connect(self) -> _MySQLConnection:
        raw = self._pymysql.connect(
            host=self.settings.host,
            port=self.settings.port,
            user=self.settings.user,
            password=self.settings.password,
            database=self.settings.database,
            charset=self.settings.charset,
            autocommit=False,
            cursorclass=self._pymysql.cursors.DictCursor,
        )
        return _MySQLConnection(raw, self._pymysql.err.IntegrityError)

    def _migrate(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS provider_connections (
                id VARCHAR(64) PRIMARY KEY,
                provider VARCHAR(64) NOT NULL,
                name VARCHAR(255) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'ready',
                description TEXT NOT NULL,
                settings_json LONGTEXT NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at VARCHAR(64) NOT NULL
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS endpoints (
                id VARCHAR(64) PRIMARY KEY,
                connection_id VARCHAR(64) NOT NULL,
                name VARCHAR(255) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                address VARCHAR(512) NOT NULL,
                routing_mode VARCHAR(32) NOT NULL DEFAULT 'fixed',
                default_scenario VARCHAR(255),
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at VARCHAR(64) NOT NULL,
                UNIQUE KEY endpoints_connection_address (connection_id, address),
                CONSTRAINT endpoints_connection_fk FOREIGN KEY (connection_id)
                    REFERENCES provider_connections(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS provider_numbers (
                id VARCHAR(64) PRIMARY KEY,
                provider VARCHAR(64) NOT NULL,
                connection_id VARCHAR(64) NOT NULL,
                endpoint_id VARCHAR(64),
                provider_resource_id VARCHAR(128) NOT NULL,
                phone_number VARCHAR(64) NOT NULL,
                friendly_name VARCHAR(255) NOT NULL,
                voice_url TEXT NOT NULL,
                voice_method VARCHAR(16) NOT NULL DEFAULT 'POST',
                status VARCHAR(64) NOT NULL DEFAULT 'active',
                managed_mode VARCHAR(64) NOT NULL DEFAULT 'amd',
                capabilities_json LONGTEXT NOT NULL,
                configuration_json LONGTEXT NOT NULL,
                last_synced_at VARCHAR(64) NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                UNIQUE KEY provider_numbers_resource (provider, provider_resource_id),
                UNIQUE KEY provider_numbers_phone (provider, phone_number),
                INDEX provider_numbers_connection (connection_id),
                CONSTRAINT provider_numbers_connection_fk FOREIGN KEY (connection_id)
                    REFERENCES provider_connections(id) ON DELETE CASCADE,
                CONSTRAINT provider_numbers_endpoint_fk FOREIGN KEY (endpoint_id)
                    REFERENCES endpoints(id) ON DELETE SET NULL
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS directory_entries (
                id VARCHAR(64) PRIMARY KEY,
                connection_id VARCHAR(64) NOT NULL,
                extension VARCHAR(32) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                destination VARCHAR(512) NOT NULL,
                department VARCHAR(255) NOT NULL DEFAULT 'General',
                ring_timeout INTEGER NOT NULL DEFAULT 25,
                enabled TINYINT(1) NOT NULL DEFAULT 1,
                created_at VARCHAR(64) NOT NULL,
                CONSTRAINT directory_connection_fk FOREIGN KEY (connection_id)
                    REFERENCES provider_connections(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS simulation_runs (
                id VARCHAR(64) PRIMARY KEY,
                endpoint_id VARCHAR(64) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                scenario VARCHAR(255) NOT NULL,
                status VARCHAR(64) NOT NULL,
                caller_number VARCHAR(128),
                extension VARCHAR(32),
                destination VARCHAR(512),
                outcome VARCHAR(255),
                duration_seconds INTEGER,
                timeline_json LONGTEXT NOT NULL,
                result_json LONGTEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                completed_at VARCHAR(64),
                INDEX simulation_runs_created_at (created_at),
                CONSTRAINT runs_endpoint_fk FOREIGN KEY (endpoint_id)
                    REFERENCES endpoints(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS incoming_calls (
                id VARCHAR(128) PRIMARY KEY,
                provider VARCHAR(64) NOT NULL,
                direction VARCHAR(32) NOT NULL DEFAULT 'inbound',
                from_address VARCHAR(512) NOT NULL DEFAULT '',
                to_address VARCHAR(512) NOT NULL DEFAULT '',
                scenario VARCHAR(255),
                status VARCHAR(64) NOT NULL,
                recording_status VARCHAR(64) NOT NULL DEFAULT 'not_started',
                started_at VARCHAR(64) NOT NULL,
                ended_at VARCHAR(64),
                duration_s DOUBLE,
                analysis_json LONGTEXT NOT NULL,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                INDEX incoming_calls_started_at (started_at)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS call_recordings (
                id VARCHAR(128) PRIMARY KEY,
                call_id VARCHAR(128) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                status VARCHAR(64) NOT NULL,
                duration_s DOUBLE,
                channels INTEGER,
                provider_url TEXT,
                local_path TEXT,
                created_at VARCHAR(64) NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                INDEX call_recordings_call_id (call_id),
                CONSTRAINT recordings_call_fk FOREIGN KEY (call_id)
                    REFERENCES incoming_calls(id) ON DELETE CASCADE
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS deleted_incoming_calls (
                id VARCHAR(128) PRIMARY KEY,
                deleted_at VARCHAR(64) NOT NULL
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS managed_provider_addresses (
                provider VARCHAR(64) NOT NULL,
                address VARCHAR(512) NOT NULL,
                first_managed_at VARCHAR(64) NOT NULL,
                PRIMARY KEY (provider, address)
            ) ENGINE=InnoDB
            """,
            """
            CREATE TABLE IF NOT EXISTS telephony_run_queue (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                provider VARCHAR(64) NOT NULL,
                address VARCHAR(512) NOT NULL,
                scenario VARCHAR(255) NOT NULL,
                run_id VARCHAR(64),
                created_at VARCHAR(64) NOT NULL,
                INDEX telephony_queue_provider_address (provider, address, id)
            ) ENGINE=InnoDB
            """,
        )
        with self._connect() as db:
            for statement in statements:
                db.execute(statement)

    def dequeue_provider_run(
        self,
        provider: str,
        address: str,
    ) -> dict[str, str | None] | None:
        return self._retry_deadlocked_transaction(
            lambda: self._dequeue_provider_run(provider, address)
        )

    def cancel_queued_run(self, run_id: str) -> SimulationRun | None:
        cancel = super().cancel_queued_run
        return self._retry_deadlocked_transaction(lambda: cancel(run_id))

    def _call_is_deleted_for_write(self, db: _MySQLConnection, call_id: str) -> bool:
        # A current locking read prevents a late callback from using an old
        # snapshot after a deletion commits. BEGIN IMMEDIATE guarantees the
        # repeatable-read gap lock when the tombstone does not exist yet.
        return db.execute(
            "SELECT 1 FROM deleted_incoming_calls WHERE id = ? FOR UPDATE", (call_id,)
        ).fetchone() is not None

    def upsert_incoming_call(self, **values: Any) -> IncomingCall | None:
        upsert = super().upsert_incoming_call
        return self._retry_deadlocked_transaction(lambda: upsert(**values))

    def upsert_recording(self, **values: Any) -> CallRecording | None:
        upsert = super().upsert_recording
        return self._retry_deadlocked_transaction(lambda: upsert(**values))

    def tombstone_incoming_call(self, call_id: str) -> None:
        tombstone = super().tombstone_incoming_call
        self._retry_deadlocked_transaction(lambda: tombstone(call_id))

    def delete_incoming_call(self, call_id: str) -> bool:
        delete = super().delete_incoming_call
        return self._retry_deadlocked_transaction(lambda: delete(call_id))

    def _retry_deadlocked_transaction(self, transaction: Callable[[], _Result]) -> _Result:
        """Restart a complete rolled-back transaction, at most four attempts.

        InnoDB error 1213 rolls back the entire transaction. Retrying a single
        statement could lose its earlier writes or locks, so each attempt must
        open a new connection and repeat the operation from BEGIN. Connection
        failures, lock timeouts, and other errors are deliberately not retried.
        """

        attempt = 0
        while True:
            try:
                return transaction()
            except self._pymysql.err.OperationalError as exc:
                attempt += 1
                if exc.args[:1] != (1213,) or attempt >= 4:
                    raise
                time.sleep(0.01 * 2 ** (attempt - 1))

    def _dequeue_provider_run(
        self,
        provider: str,
        address: str,
    ) -> dict[str, str | None] | None:
        """Consume one assignment under a MySQL row lock."""

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT id, scenario, run_id
                FROM telephony_run_queue
                WHERE provider = ? AND address = ?
                ORDER BY id
                LIMIT 1
                FOR UPDATE
                """,
                (provider, address),
            ).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM telephony_run_queue WHERE id = ?", (row["id"],))
        return {"scenario": str(row["scenario"]), "run_id": row["run_id"]}
