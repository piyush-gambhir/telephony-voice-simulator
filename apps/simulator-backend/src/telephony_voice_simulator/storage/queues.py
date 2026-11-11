"""Persistence for queues, shared by SQLite and MySQL."""

from __future__ import annotations

import json
from typing import Any

from .common import _id, _now


class QueuesStoreMixin:
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
        call_id: str | None = None,
    ) -> dict[str, str | None] | None:
        """Atomically consume the oldest persisted provider assignment.

        With ``call_id`` the claim is saved on that call in the same transaction,
        and a retry for the same call returns its existing claim.
        """

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            claimed = self._existing_queue_claim(db, call_id)
            if claimed is not None:
                return claimed
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
            return self._claim_queue_row(db, row, provider=provider, address=address, call_id=call_id)

    def _existing_queue_claim(self, db: Any, call_id: str | None) -> dict[str, str | None] | None:
        if not call_id:
            return None
        row = db.execute(
            "SELECT analysis_json FROM incoming_calls WHERE id = ?" + self._LOCKING_READ, (call_id,)
        ).fetchone()
        claim = json.loads(row["analysis_json"]).get("queue_claim") if row else None
        return dict(claim) if isinstance(claim, dict) else None

    def _claim_queue_row(
        self,
        db: Any,
        row: Any,
        *,
        provider: str,
        address: str,
        call_id: str | None,
    ) -> dict[str, str | None] | None:
        """Delete one queue row and, for a call, record the claim before commit."""

        if row is None or (call_id and self._call_is_deleted_for_write(db, call_id)):
            return None
        claim = {"scenario": str(row["scenario"]), "run_id": row["run_id"]}
        db.execute("DELETE FROM telephony_run_queue WHERE id = ?", (row["id"],))
        if call_id:
            timestamp = _now()
            db.execute(
                """
                INSERT INTO incoming_calls (
                    id, provider, to_address, status, started_at, analysis_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'in-progress', ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET analysis_json = excluded.analysis_json
                """,
                (call_id, provider, address, timestamp,
                 json.dumps({"run_id": claim["run_id"], "queue_claim": claim}),
                 timestamp, timestamp),
            )
            if claim["run_id"]:
                # A claimed run can no longer be cancelled, so stop showing it as queued.
                db.execute(
                    """UPDATE simulation_runs SET status = 'in-progress'
                       WHERE id = ? AND status = 'queued' AND completed_at IS NULL""",
                    (claim["run_id"],),
                )
        return claim

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
            # Lock queue rows before runs, like a claim, so a concurrent claim either
            # finishes first (its run is no longer queued here) or waits for this.
            db.execute(
                "SELECT id FROM telephony_run_queue WHERE provider = ? AND address = ?"
                + self._LOCKING_READ,
                (provider, address),
            ).fetchall()
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

    @staticmethod
    def _queued_persona(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "number": row["number"],
            "persona": json.loads(row["persona_json"]),
            "label": row["label"],
            "created_at": row["created_at"],
        }

    def enqueue_customer_persona(
        self, number: str, persona: dict[str, Any], label: str | None = None
    ) -> dict[str, Any]:
        """Queue a persona for the next call on a customer line."""
        row = {
            "id": _id("persona"),
            "number": number,
            "persona_json": json.dumps(persona),
            "label": label,
            "created_at": _now(),
        }
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO customer_persona_queue (id, number, persona_json, label, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                tuple(row.values()),
            )
        return self._queued_persona(row)

    def list_customer_persona_queue(self, number: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM customer_persona_queue"
        params: tuple[object, ...] = ()
        if number:
            query += " WHERE number = ?"
            params = (number,)
        with self._connect() as db:
            rows = db.execute(query + " ORDER BY created_at, id", params).fetchall()
        return [self._queued_persona(row) for row in rows]

    def cancel_customer_persona(self, queue_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM customer_persona_queue WHERE id = ?", (queue_id,))
            return cursor.rowcount > 0

    def claim_customer_persona(self, number: str) -> dict[str, Any] | None:
        """Atomically take the oldest persona queued for ``number``."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM customer_persona_queue WHERE number = ?"
                " ORDER BY created_at, id LIMIT 1" + self._LOCKING_READ,
                (number,),
            ).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM customer_persona_queue WHERE id = ?", (row["id"],))
            return self._queued_persona(row)
