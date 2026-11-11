"""Persistence for calls, shared by SQLite and MySQL."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import (
    CallRecording,
    IncomingCall,
)
from .common import _now


class CallsStoreMixin:
    # Keep the outcome while removing deleted call content from linked runs.
    _RUN_FIELDS_KEPT = ("mode", "call_id", "passed", "graded", "summary", "carrier_status",
                        "duration_seconds")

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
        direction: str = "inbound",
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
            if self._call_is_deleted_for_write(db, call_id):
                return None
            db.execute(
                """
                INSERT INTO incoming_calls (
                    id, provider, direction, from_address, to_address, scenario,
                    status, recording_status, started_at, ended_at, duration_s,
                    analysis_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    from_address = CASE WHEN excluded.from_address = '' THEN from_address
                                        ELSE excluded.from_address END,
                    to_address = CASE WHEN excluded.to_address = '' THEN to_address
                                      ELSE excluded.to_address END,
                    scenario = COALESCE(excluded.scenario, scenario),
                    status = CASE
                        WHEN incoming_calls.status IN ('completed','failed','busy','no-answer','canceled')
                         AND excluded.status NOT IN ('completed','failed','busy','no-answer','canceled')
                        THEN incoming_calls.status ELSE excluded.status END,
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
                    direction,
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
            if self._call_is_deleted_for_write(db, call_id):
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

    def list_calls_awaiting_finalization(self) -> list[IncomingCall]:
        """Recover grading and unfinished media independently of the history page limit."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM incoming_calls AS calls
                   WHERE calls.provider = 'twilio' AND (calls.analysis_json LIKE ? OR EXISTS (
                       SELECT 1 FROM call_recordings AS media WHERE media.call_id = calls.id
                       AND media.status IN ('completed', 'download_failed')
                       AND media.local_path IS NULL
                   ))""",
                ('%"finalization"%',),
            ).fetchall()
            calls = [self._call(db, row) for row in rows]
        return [call for call in calls
                if call.analysis.get("finalization", {}).get("state") in {"pending", "error"}
                or any(recording.status in {"completed", "download_failed"} and not recording.local_path
                       for recording in call.recordings)]

    def is_incoming_call_deleted(self, call_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM deleted_incoming_calls WHERE id = ?", (call_id,)
            ).fetchone()
        return row is not None

    def _call_is_deleted_for_write(self, db: sqlite3.Connection, call_id: str) -> bool:
        """Check under the active write transaction's database-specific lock."""

        return db.execute(
            "SELECT 1 FROM deleted_incoming_calls WHERE id = ?", (call_id,)
        ).fetchone() is not None

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
            self._redact_runs_for_call(db, call_id)
            cursor = db.execute("DELETE FROM incoming_calls WHERE id = ?", (call_id,))
        return cursor.rowcount > 0

    def _redact_runs_for_call(self, db: Any, call_id: str) -> None:
        """Strip a deleted call's analysis (transcripts, numbers) from the runs it finished."""
        rows = db.execute(
            "SELECT id, result_json FROM simulation_runs WHERE result_json LIKE ?",
            (f"%{call_id}%",),
        ).fetchall()
        for row in rows:
            result = json.loads(row["result_json"] or "{}")
            if result.get("call_id") != call_id:
                continue
            kept = {k: result[k] for k in self._RUN_FIELDS_KEPT if k in result}
            db.execute(
                "UPDATE simulation_runs SET result_json = ? WHERE id = ?",
                (json.dumps({**kept, "call_deleted": True}), row["id"]),
            )

    def get_recording(self, recording_id: str) -> CallRecording | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM call_recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        return self._recording(row) if row else None
