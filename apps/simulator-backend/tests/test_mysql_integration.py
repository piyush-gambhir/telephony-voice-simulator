"""Optional real-MySQL checks; use a dedicated, disposable database.

Set SIMULATOR_TEST_MYSQL_URI explicitly to enable these tests. They never
contact a carrier and clean up the records they create.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import os
from threading import Barrier, Event
from uuid import uuid4

import pytest

from telephony_voice_simulator.control.mysql_store import MySQLSimulatorStore
from telephony_voice_simulator.control.service import SimulatorService
from telephony_voice_simulator.control.store import StoreConflictError
from telephony_voice_simulator.telephony.providers.mock import MockProvider

URI = os.environ.get("SIMULATOR_TEST_MYSQL_URI")
pytestmark = pytest.mark.skipif(not URI, reason="requires a disposable SIMULATOR_TEST_MYSQL_URI")


@pytest.fixture
def workspace():
    store = MySQLSimulatorStore(URI)
    connection = store.create_connection("mock", f"MySQL test {uuid4().hex}")
    endpoint = store.create_endpoint(
        connection_id=connection.id, name="Test line", kind="extension", address="1001",
        default_scenario="tone_only_voicemail",
    )
    try:
        yield store, connection, endpoint
    finally:
        with store._connect() as database:
            database.execute(
                "DELETE FROM telephony_run_queue WHERE run_id IN "
                "(SELECT id FROM simulation_runs WHERE endpoint_id = ?)", (endpoint.id,),
            )
        store.delete_connection(connection.id)
        store.close()


async def test_mysql_service_round_trip_and_reopened_repository(workspace):
    store, connection, endpoint = workspace
    service = SimulatorService(store=store, providers={"mock": MockProvider()})
    run = await service.create_run(endpoint.id)
    assert run["status"] == "completed"
    assert run["result"]["graded"] is False

    reopened = MySQLSimulatorStore(URI)
    try:
        assert reopened.get_connection(connection.id).name == connection.name
        persisted = reopened.get_run(run["id"])
        assert persisted.result == run["result"]
        assert persisted.timeline == run["timeline"]
        assert persisted.completed_at
    finally:
        reopened.close()


def test_mysql_conflict_rolls_back_related_writes(workspace):
    store, connection, endpoint = workspace
    with pytest.raises(StoreConflictError):
        with store._connect() as database:
            database.execute(
                "UPDATE provider_connections SET name = ? WHERE id = ?",
                ("Must roll back", connection.id),
            )
            database.execute(
                "UPDATE endpoints SET connection_id = ? WHERE id = ?",
                ("missing-provider", endpoint.id),
            )
    assert store.get_connection(connection.id).name == connection.name
    assert store.get_endpoint(endpoint.id).connection_id == connection.id


def test_mysql_call_recording_upserts_and_deletion_tombstone(workspace):
    store, _, _ = workspace
    call_id = f"CA{uuid4().hex}"
    recording_id = f"RE{uuid4().hex}"
    try:
        store.upsert_incoming_call(call_id=call_id, from_address="+15550100001")
        store.upsert_incoming_call(
            call_id=call_id, status="completed", duration_s=2.5,
            analysis={"passed": None, "checks": []},
        )
        store.upsert_recording(
            recording_id=recording_id, call_id=call_id, provider="mock",
            kind="message", status="processing", local_path="/tmp/synthetic.wav",
        )
        store.upsert_recording(
            recording_id=recording_id, call_id=call_id, provider="mock",
            kind="message", status="completed", duration_s=2.5,
        )
        call = store.get_incoming_call(call_id)
        assert call.from_address == "+15550100001"
        assert call.analysis == {"passed": None, "checks": []}
        assert len(call.recordings) == 1
        assert call.recordings[0].local_path == "/tmp/synthetic.wav"
        assert store.delete_incoming_call(call_id)
        assert store.get_recording(recording_id) is None
        assert store.upsert_incoming_call(call_id=call_id) is None
        assert store.upsert_recording(
            recording_id=recording_id, call_id=call_id, provider="mock",
            kind="message", status="completed",
        ) is None
    finally:
        store.delete_incoming_call(call_id)
        with store._connect() as database:
            database.execute("DELETE FROM deleted_incoming_calls WHERE id = ?", (call_id,))


def test_mysql_cancel_and_claim_have_exactly_one_winner(workspace):
    store, _, endpoint = workspace
    # Repeat with real competing connections to cover both lock-acquisition orders.
    for _ in range(20):
        run = store.create_run(endpoint.id, "mock", "tone_only_voicemail")
        store.update_run(run, status="queued")
        store.enqueue_provider_run(
            provider="mock", address=endpoint.id, scenario=run.scenario, run_id=run.id,
        )
        barrier = Barrier(2)

        def cancel():
            barrier.wait()
            return store.cancel_queued_run(run.id)

        def claim():
            barrier.wait()
            return store.dequeue_provider_run("mock", endpoint.id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            cancellation = pool.submit(cancel)
            assignment = pool.submit(claim)
            cancelled, claimed = cancellation.result(), assignment.result()
        assert (cancelled is not None) != (claimed is not None)
        assert store.list_provider_queue("mock", endpoint.id) == []
        assert store.get_run(run.id).status == ("cancelled" if cancelled else "queued")


@pytest.mark.parametrize("operation", ["cancel", "claim"])
@pytest.mark.parametrize(
    "error_code,always_fail,expected_attempts",
    [(1213, False, 2), (1213, True, 4), (1205, False, 1), (2006, False, 1)],
)
def test_mysql_queue_retries_whole_deadlocked_transactions_only(
    workspace, monkeypatch, operation, error_code, always_fail, expected_attempts,
):
    store, _, endpoint = workspace
    run = store.create_run(endpoint.id, "mock", "tone_only_voicemail")
    store.update_run(run, status="queued")
    store.enqueue_provider_run(
        provider="mock", address=endpoint.id, scenario=run.scenario, run_id=run.id,
    )
    original_connect = store._connect
    attempts = []

    def connect():
        connection = original_connect()
        original_execute = connection.execute

        def execute(statement, parameters=()):
            cursor = original_execute(statement, parameters)
            if statement.strip() == "BEGIN IMMEDIATE":
                attempts.append(connection)
            if statement.startswith("DELETE FROM telephony_run_queue") and (
                always_fail or len(attempts) == 1
            ):
                # The real DELETE has already executed. Retrying only this
                # statement, or losing rollback, would leave no assignment to
                # claim/cancel on the next attempt and fail the assertions.
                raise store._pymysql.err.OperationalError(error_code, "injected database failure")
            return cursor

        connection.execute = execute
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", connect)
        if error_code == 1213 and not always_fail:
            result = (
                store.cancel_queued_run(run.id) if operation == "cancel"
                else store.dequeue_provider_run("mock", endpoint.id)
            )
            assert result is not None
        else:
            with pytest.raises(store._pymysql.err.OperationalError) as error:
                if operation == "cancel":
                    store.cancel_queued_run(run.id)
                else:
                    store.dequeue_provider_run("mock", endpoint.id)
            assert error.value.args[0] == error_code
    assert len(attempts) == expected_attempts
    assert len({id(connection) for connection in attempts}) == expected_attempts
    if error_code == 1213 and not always_fail:
        assert store.list_provider_queue("mock", endpoint.id) == []
        assert store.get_run(run.id).status == ("cancelled" if operation == "cancel" else "queued")
    else:
        assert store.list_provider_queue("mock", endpoint.id) == [
            {"scenario": run.scenario, "run_id": run.id}
        ]
        assert store.get_run(run.id).status == "queued"


def test_mysql_concurrent_callback_cannot_resurrect_deleted_call(workspace):
    store, _, _ = workspace
    for _ in range(20):
        call_id = f"CA{uuid4().hex}"
        store.upsert_incoming_call(call_id=call_id)
        barrier = Barrier(2)

        def delete():
            barrier.wait()
            return store.delete_incoming_call(call_id)

        def late_callback():
            barrier.wait()
            return store.upsert_incoming_call(call_id=call_id, status="completed")

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                deletion = pool.submit(delete)
                callback = pool.submit(late_callback)
                assert deletion.result()
                callback.result()
            assert store.is_incoming_call_deleted(call_id)
            assert store.get_incoming_call(call_id) is None
        finally:
            store.delete_incoming_call(call_id)
            with store._connect() as database:
                database.execute("DELETE FROM deleted_incoming_calls WHERE id = ?", (call_id,))


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "READ COMMITTED"])
@pytest.mark.parametrize("callback_kind", ["call", "recording"])
def test_mysql_deletion_wins_when_callback_pauses_after_tombstone_check(
    workspace, monkeypatch, isolation, callback_kind,
):
    store, _, _ = workspace
    callback_store = MySQLSimulatorStore(URI)
    call_id = f"CA{uuid4().hex}"
    recording_id = f"RE{uuid4().hex}"
    store.upsert_incoming_call(call_id=call_id)
    checked = Event()
    release = Event()
    original_connect = callback_store._connect

    def connect():
        connection = original_connect()
        original_execute = connection.execute
        original_execute(f"SET SESSION TRANSACTION ISOLATION LEVEL {isolation}")

        def execute(statement, parameters=()):
            cursor = original_execute(statement, parameters)
            if statement.startswith("SELECT 1 FROM deleted_incoming_calls"):
                checked.set()
                assert release.wait(timeout=5), "Test did not release the paused callback"
            return cursor

        connection.execute = execute
        return connection

    monkeypatch.setattr(callback_store, "_connect", connect)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            callback = (
                pool.submit(callback_store.upsert_incoming_call, call_id=call_id)
                if callback_kind == "call"
                else pool.submit(
                    callback_store.upsert_recording, recording_id=recording_id,
                    call_id=call_id, provider="mock", kind="message", status="completed",
                )
            )
            assert checked.wait(timeout=5), "Callback never checked the tombstone"
            deletion = pool.submit(store.delete_incoming_call, call_id)
            try:
                # A correct lock keeps deletion pending until the callback can
                # finish. Without it, deletion commits before callback's INSERT.
                deletion.result(timeout=0.2)
            except TimeoutError:
                pass
            finally:
                release.set()
            callback.result(timeout=5)
            assert deletion.result(timeout=5)
        assert store.is_incoming_call_deleted(call_id)
        assert store.get_incoming_call(call_id) is None
        assert store.get_recording(recording_id) is None
    finally:
        release.set()
        store.delete_incoming_call(call_id)
        with store._connect() as database:
            database.execute("DELETE FROM deleted_incoming_calls WHERE id = ?", (call_id,))
        callback_store.close()
