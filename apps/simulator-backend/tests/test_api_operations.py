"""Operations: background jobs, their log, conflicts and restart cleanup."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.api.app import build_app
from telephony_voice_simulator.service import ConflictError, SimulatorService, ValidationError
from telephony_voice_simulator.storage.sqlite import SimulatorStore


@pytest.fixture()
def service(tmp_path: Path) -> SimulatorService:
    service = SimulatorService(store=SimulatorStore(tmp_path / "operations.db"))
    yield service
    if service._runner is not None:
        service._runner.shutdown()


async def _finished(service: SimulatorService, operation_id: str) -> dict:
    for _ in range(200):
        operation = service.get_operation(operation_id)
        if operation["status"] in {"succeeded", "failed"}:
            return operation
        await asyncio.sleep(0.01)
    raise AssertionError("operation did not finish")


async def test_operation_records_log_lines_and_result(service: SimulatorService) -> None:
    def job(log):
        log("first step")
        log("second step")
        return {"answer": 42}

    started = service.start_operation("demo", "Demo job", {"size": 1}, job)
    assert started["status"] == "queued"

    finished = await _finished(service, started["id"])
    assert finished["status"] == "succeeded"
    assert finished["log"] == ["first step", "second step"]
    assert finished["result"] == {"answer": 42}
    assert finished["params"] == {"size": 1}


async def test_async_jobs_run_to_completion(service: SimulatorService) -> None:
    async def job(log):
        await asyncio.sleep(0)
        log("awaited")
        return "done"

    started = service.start_operation("demo", "Async job", {}, job)
    finished = await _finished(service, started["id"])
    assert (finished["status"], finished["result"]) == ("succeeded", "done")


async def test_service_errors_become_readable_failures(service: SimulatorService) -> None:
    def job(_log):
        raise ValidationError("Pick a scenario first")

    started = service.start_operation("demo", "Failing job", {}, job)
    finished = await _finished(service, started["id"])
    assert finished["status"] == "failed"
    assert finished["error"] == "Pick a scenario first"


async def test_one_operation_of_a_kind_runs_at_a_time(service: SimulatorService) -> None:
    release = threading.Event()

    def job(_log):
        release.wait(5)

    first = service.start_operation("build", "First", {}, job)
    with pytest.raises(ConflictError, match="already running"):
        service.start_operation("build", "Second", {}, job)
    release.set()
    await _finished(service, first["id"])
    second = service.start_operation("build", "Third", {}, lambda _log: None)
    assert (await _finished(service, second["id"]))["status"] == "succeeded"


async def test_api_startup_fails_operations_a_previous_process_left_open(
    service: SimulatorService,
) -> None:
    stale = service.store.create_operation("build", "Interrupted", {})
    service.store.start_operation(stale.id)

    async with TestClient(TestServer(build_app(service))) as client:
        response = await client.get(f"/api/operations/{stale.id}")
        operation = await response.json()
        listing = await (await client.get("/api/operations?kind=build")).json()

    assert operation["status"] == "failed"
    assert "restarted" in operation["error"]
    assert [item["id"] for item in listing] == [stale.id]


async def test_unknown_operation_is_404(service: SimulatorService) -> None:
    async with TestClient(TestServer(build_app(service))) as client:
        response = await client.get("/api/operations/op_missing")
    assert response.status == 404


async def test_a_failed_start_releases_its_kind(service: SimulatorService, monkeypatch) -> None:
    original = service.store.create_operation

    def broken(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service.store, "create_operation", broken)
    with pytest.raises(RuntimeError):
        service.start_operation("build", "First", {}, lambda _log: None)
    monkeypatch.setattr(service.store, "create_operation", original)

    started = service.start_operation("build", "x" * 400, {}, lambda _log: None)
    assert len(started["title"]) == 200
    assert (await _finished(service, started["id"]))["status"] == "succeeded"


async def test_log_lines_from_concurrent_threads_are_all_kept(service: SimulatorService) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def job(log):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda index: log(f"lane {index}"), range(40)))

    started = service.start_operation("benchmark", "Lanes", {}, job)
    finished = await _finished(service, started["id"])
    assert sorted(finished["log"]) == sorted(f"lane {index}" for index in range(40))
