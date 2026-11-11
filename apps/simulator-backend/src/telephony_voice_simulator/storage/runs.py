"""Persistence for runs, shared by SQLite and MySQL."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any

from ..models import (
    SimulationRun,
)
from .common import StoreConflictError, _id, _now


class RunsStoreMixin:
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

    def cancel_queued_run(self, run_id: str) -> SimulationRun | None:
        """Cancel only work that has not been claimed by an incoming call.

        Deleting the queue assignment first arbitrates with concurrent consumers
        on both SQLite and MySQL. A claimed assignment can never be cancelled.
        """

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            deleted = db.execute(
                "DELETE FROM telephony_run_queue WHERE run_id = ?", (run_id,)
            ).rowcount
            if not deleted:
                return None
            row = db.execute(
                "SELECT * FROM simulation_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "queued" or row["completed_at"] is not None:
                # Raising rolls back the assignment deletion as well.
                raise StoreConflictError("Only queued runs can be cancelled")
            result = json.loads(row["result_json"])
            result.update({"graded": False, "summary": "Cancelled before an incoming call arrived."})
            timestamp = _now()
            db.execute(
                """UPDATE simulation_runs
                   SET status = 'cancelled', result_json = ?, completed_at = ?
                   WHERE id = ?""",
                (json.dumps(result), timestamp, run_id),
            )
        return replace(
            self._run(row), status="cancelled", result=result, completed_at=timestamp
        )

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
