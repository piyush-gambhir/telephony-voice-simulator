from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

import pytest

from telephony_voice_simulator.control.database import (
    create_simulator_store,
    parse_mysql_uri,
)
from telephony_voice_simulator.control.store import (
    SQLiteSimulatorStore, SimulatorStore, StoreConflictError,
)
from telephony_voice_simulator.telephony.base import RunRequest
from telephony_voice_simulator.telephony.providers.twilio import TwilioProvider


def test_sqlite_database_uri_selects_local_repository(tmp_path: Path) -> None:
    database = tmp_path / "configured.db"
    store = create_simulator_store(uri=f"sqlite:///{database}")

    assert isinstance(store, SQLiteSimulatorStore)
    assert store.path == database
    assert database.exists()


def test_memory_repositories_persist_across_operations_and_are_isolated() -> None:
    first = create_simulator_store(uri="sqlite:///:memory:")
    second = create_simulator_store(uri="sqlite:///:memory:")
    try:
        connection = first.create_connection("mock", "Memory")
        assert first.get_connection(connection.id) == connection
        assert second.list_connections() == []
    finally:
        first.close()
        second.close()
    with pytest.raises(RuntimeError, match="closed"):
        first.list_connections()


def test_sqlite_transactions_close_connections_and_rollback_on_conflict(tmp_path: Path) -> None:
    store = SQLiteSimulatorStore(tmp_path / "transactions.db")
    connection = store.create_connection("mock", "Local")
    with store._connect() as database:
        assert database.execute("SELECT COUNT(*) FROM provider_connections").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        database.execute("SELECT 1")
    with pytest.raises(StoreConflictError):
        with store._connect() as database:
            database.execute("DELETE FROM provider_connections WHERE id = ?", (connection.id,))
            database.execute(
                "INSERT INTO provider_connections (id) VALUES (?)", ("invalid",)
            )
    assert store.get_connection(connection.id) == connection


def test_cancellation_and_incoming_call_claim_cannot_both_consume_a_run(tmp_path: Path) -> None:
    store = SQLiteSimulatorStore(tmp_path / "race.db")
    connection = store.create_connection("mock", "Local")
    endpoint = store.create_endpoint(
        connection_id=connection.id, name="Queue", kind="extension", address="1001"
    )
    run = store.create_run(endpoint.id, "mock", "human")
    store.update_run(run, status="queued")
    store.enqueue_provider_run(
        provider="mock", address=endpoint.address, scenario=run.scenario, run_id=run.id
    )
    barrier = Barrier(2)

    def cancel():
        barrier.wait()
        return store.cancel_queued_run(run.id)

    def claim():
        barrier.wait()
        return store.dequeue_provider_run("mock", endpoint.address)

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelled_future = pool.submit(cancel)
        claimed_future = pool.submit(claim)
        cancelled, claimed = cancelled_future.result(), claimed_future.result()
    assert (cancelled is not None) != (claimed is not None)
    assert store.list_provider_queue("mock", endpoint.address) == []
    persisted = store.get_run(run.id)
    assert persisted.status == ("cancelled" if cancelled else "queued")


def test_explicit_path_precedes_database_uri_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SIMULATOR_DATABASE_URI",
        "mysql://not-used:not-used@database.invalid/not_used",
    )
    database = tmp_path / "explicit.db"

    store = SimulatorStore(database)

    assert isinstance(store, SQLiteSimulatorStore)
    assert store.path == database


def test_mysql_uri_parser_decodes_credentials_and_options() -> None:
    settings = parse_mysql_uri(
        "mysql+pymysql://sim%40user:s%2Fcret@db.internal:3307/voice%2Dsim"
        "?charset=utf8mb4"
    )

    assert settings.host == "db.internal"
    assert settings.port == 3307
    assert settings.user == "sim@user"
    assert settings.password == "s/cret"
    assert settings.database == "voice-sim"
    assert settings.charset == "utf8mb4"


def test_sqlite_migrates_and_removes_twilio_only_tables(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE managed_twilio_addresses (
                address TEXT PRIMARY KEY,
                first_managed_at TEXT NOT NULL
            );
            INSERT INTO managed_twilio_addresses VALUES ('+15550101000', '2026-01-01');
            CREATE TABLE pstn_run_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                scenario TEXT NOT NULL,
                run_id TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO pstn_run_queue (address, scenario, run_id, created_at)
            VALUES ('+15550101000', 'machine', NULL, '2026-01-01');
            """
        )

    store = SimulatorStore(database)

    assert store.list_managed_provider_addresses("twilio") == ["+15550101000"]
    assert store.list_provider_queue("twilio", "+15550101000") == [
        {"scenario": "machine", "run_id": None}
    ]
    with sqlite3.connect(database) as connection:
        old_tables = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name IN ('managed_twilio_addresses', 'pstn_run_queue')
            """
        ).fetchall()
    assert old_tables == []


class _AvailableRuntime:
    def scenario_available(self, scenario_name: str) -> bool:
        return scenario_name == "machine"


@pytest.mark.asyncio
async def test_twilio_adapter_owns_queue_dispatch(tmp_path: Path) -> None:
    store = SimulatorStore(tmp_path / "provider.db")
    connection = store.create_connection("twilio", "Twilio")
    endpoint = store.create_endpoint(
        connection_id=connection.id,
        name="Inbound",
        kind="phone_number",
        address="+15550101000",
        routing_mode="queued",
    )
    provider = TwilioProvider(store=store, runtime=_AvailableRuntime())

    dispatched = await provider.dispatch(
        RunRequest(
            run_id="run_provider",
            connection=connection,
            endpoint=endpoint,
            scenario={"name": "machine", "kind": "amd"},
        )
    )

    assert dispatched.status == "queued"
    assert store.list_provider_queue("twilio", endpoint.address) == [
        {"scenario": "machine", "run_id": "run_provider"}
    ]
