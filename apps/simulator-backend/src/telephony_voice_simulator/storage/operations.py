"""Persistence for operations, shared by SQLite and MySQL."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import (
    Operation,
)
from .common import _id, _now


class OperationsStoreMixin:
    OPERATION_LOG_LIMIT = 1000

    @staticmethod
    def _operation(row: sqlite3.Row) -> Operation:
        return Operation(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            status=row["status"],
            params=json.loads(row["params_json"]),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            log=json.loads(row["log_json"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def create_operation(self, kind: str, title: str, params: dict[str, Any]) -> Operation:
        record = Operation(
            id=_id("op"), kind=kind, title=title, status="queued", params=params,
            created_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO operations (
                    id, kind, title, status, params_json, log_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, '[]', ?)
                """,
                (record.id, kind, title, record.status, json.dumps(params, default=str),
                 record.created_at),
            )
        return record

    def get_operation(self, operation_id: str) -> Operation | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
        return self._operation(row) if row else None

    def list_operations(self, limit: int = 50, kind: str | None = None) -> list[Operation]:
        with self._connect() as db:
            if kind:
                rows = db.execute(
                    "SELECT * FROM operations WHERE kind = ? ORDER BY created_at DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._operation(row) for row in rows]

    def start_operation(self, operation_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE operations SET status = 'running', started_at = ? WHERE id = ?",
                (_now(), operation_id),
            )

    def finish_operation(
        self,
        operation_id: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE operations
                SET status = ?, result_json = ?, error = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    "failed" if error is not None else "succeeded",
                    json.dumps(result, default=str) if error is None else None,
                    error,
                    _now(),
                    operation_id,
                ),
            )

    def append_operation_log(self, operation_id: str, lines: list[str]) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT log_json FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                return
            log = [*json.loads(row["log_json"]), *lines][-self.OPERATION_LOG_LIMIT:]
            db.execute(
                "UPDATE operations SET log_json = ? WHERE id = ?",
                (json.dumps(log), operation_id),
            )

    def fail_unfinished_operations(self, reason: str) -> int:
        """Close out jobs a previous process started; none of them can resume."""
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE operations SET status = 'failed', error = ?, finished_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (reason, _now()),
            )
            return cursor.rowcount
