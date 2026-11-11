"""Persistence for resources, shared by SQLite and MySQL."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any

from ..models import (
    DirectoryEntry,
    Endpoint,
    ProviderConnection,
)
from .common import _id, _now


class ResourcesStoreMixin:
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
