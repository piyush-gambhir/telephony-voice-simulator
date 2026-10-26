"""Run long jobs in the background and record their progress as operations.

The API starts a job and returns at once with an operation; the console polls
it for log lines and the result. Jobs run in this process: a restart marks any
unfinished operation as failed, since none of them can resume.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..storage.sqlite import SimulatorStore
from .errors import ConflictError, NotFoundError, ValidationError

Log = Callable[[str], None]
Job = Callable[[Log], Any]


class OperationRunner:
    def __init__(self, store: SimulatorStore, *, workers: int = 4) -> None:
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="operation")
        self._lock = threading.Lock()
        self._active_kinds: set[str] = set()

    def start(self, kind: str, title: str, params: dict[str, Any], job: Job) -> dict[str, Any]:
        """Queue ``job``; only one operation of each kind runs at a time."""
        with self._lock:
            if kind in self._active_kinds:
                raise ConflictError(f"A {kind} operation is already running")
            self._active_kinds.add(kind)
        operation = self.store.create_operation(kind, title, params)
        self._executor.submit(self._run, operation.id, kind, job)
        return operation.as_dict()

    def _run(self, operation_id: str, kind: str, job: Job) -> None:
        def log(line: str) -> None:
            self.store.append_operation_log(operation_id, [str(line)])

        try:
            self.store.start_operation(operation_id)
            result = job(log)
            if inspect.isawaitable(result):
                result = asyncio.run(_awaited(result))
        except (ValidationError, NotFoundError, ConflictError) as exc:
            self.store.finish_operation(operation_id, error=str(exc))
        except Exception as exc:  # every failure belongs in the operation record
            self.store.finish_operation(operation_id, error=f"{type(exc).__name__}: {exc}")
        else:
            self.store.finish_operation(operation_id, result=result)
        finally:
            with self._lock:
                self._active_kinds.discard(kind)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


async def _awaited(awaitable: Any) -> Any:
    return await awaitable


class OperationsMixin:
    """Operation reads and job start, on the shared service."""

    _runner: OperationRunner | None = None

    @property
    def operations(self) -> OperationRunner:
        # Built on first use: CLI commands run jobs inline and never need threads.
        if self._runner is None:
            self._runner = OperationRunner(self.store)
        return self._runner

    def list_operations(self, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.store.list_operations(limit=limit, kind=kind)]

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        operation = self.store.get_operation(operation_id)
        if operation is None:
            raise NotFoundError(f"Unknown operation: {operation_id}")
        return operation.as_dict()

    def start_operation(
        self, kind: str, title: str, params: dict[str, Any], job: Job
    ) -> dict[str, Any]:
        return self.operations.start(kind, title, params, job)
