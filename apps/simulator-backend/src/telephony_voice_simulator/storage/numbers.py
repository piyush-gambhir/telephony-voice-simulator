"""Persistence for numbers, shared by SQLite and MySQL."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any

from ..models import (
    ProviderNumber,
)
from .common import _id, _now


class NumbersStoreMixin:
    @staticmethod
    def _provider_number(row: sqlite3.Row) -> ProviderNumber:
        return ProviderNumber(
            id=row["id"],
            provider=row["provider"],
            connection_id=row["connection_id"],
            endpoint_id=row["endpoint_id"],
            provider_resource_id=row["provider_resource_id"],
            phone_number=row["phone_number"],
            friendly_name=row["friendly_name"],
            voice_url=row["voice_url"],
            voice_method=row["voice_method"],
            status=row["status"],
            managed_mode=row["managed_mode"],
            capabilities=json.loads(row["capabilities_json"]),
            configuration=json.loads(row["configuration_json"]),
            last_synced_at=row["last_synced_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_provider_numbers(
        self,
        *,
        provider: str | None = None,
        managed_mode: str | None = None,
    ) -> list[ProviderNumber]:
        clauses: list[str] = []
        parameters: list[object] = []
        if provider is not None:
            clauses.append("provider = ?")
            parameters.append(provider)
        if managed_mode is not None:
            clauses.append("managed_mode = ?")
            parameters.append(managed_mode)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM provider_numbers{where} ORDER BY phone_number",
                tuple(parameters),
            ).fetchall()
        return [self._provider_number(row) for row in rows]

    def get_provider_number(self, number_id: str) -> ProviderNumber | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM provider_numbers WHERE id = ?",
                (number_id,),
            ).fetchone()
        return self._provider_number(row) if row else None

    def upsert_provider_number(
        self,
        *,
        provider: str,
        connection_id: str,
        endpoint_id: str | None,
        provider_resource_id: str,
        phone_number: str,
        friendly_name: str,
        voice_url: str,
        voice_method: str = "POST",
        status: str = "active",
        managed_mode: str = "amd",
        capabilities: dict[str, Any] | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ProviderNumber:
        now = _now()
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM provider_numbers
                WHERE provider = ? AND provider_resource_id = ?
                """,
                (provider, provider_resource_id),
            ).fetchone()
            if row is None:
                record = ProviderNumber(
                    id=_id("number"),
                    provider=provider,
                    connection_id=connection_id,
                    endpoint_id=endpoint_id,
                    provider_resource_id=provider_resource_id,
                    phone_number=phone_number,
                    friendly_name=friendly_name,
                    voice_url=voice_url,
                    voice_method=voice_method,
                    status=status,
                    managed_mode=managed_mode,
                    capabilities=capabilities or {},
                    configuration=configuration or {},
                    last_synced_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.execute(
                    """
                    INSERT INTO provider_numbers (
                        id, provider, connection_id, endpoint_id, provider_resource_id,
                        phone_number, friendly_name, voice_url, voice_method, status,
                        managed_mode, capabilities_json, configuration_json,
                        last_synced_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.provider,
                        record.connection_id,
                        record.endpoint_id,
                        record.provider_resource_id,
                        record.phone_number,
                        record.friendly_name,
                        record.voice_url,
                        record.voice_method,
                        record.status,
                        record.managed_mode,
                        json.dumps(record.capabilities),
                        json.dumps(record.configuration),
                        record.last_synced_at,
                        record.created_at,
                        record.updated_at,
                    ),
                )
                return record
            existing = self._provider_number(row)
            merged_configuration = (
                existing.configuration if configuration is None else configuration
            )
            merged_capabilities = existing.capabilities if capabilities is None else capabilities
            db.execute(
                """
                UPDATE provider_numbers
                SET connection_id = ?, endpoint_id = ?, phone_number = ?,
                    friendly_name = ?, voice_url = ?, voice_method = ?, status = ?,
                    managed_mode = ?, capabilities_json = ?, configuration_json = ?,
                    last_synced_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    connection_id,
                    endpoint_id,
                    phone_number,
                    friendly_name,
                    voice_url,
                    voice_method,
                    status,
                    managed_mode,
                    json.dumps(merged_capabilities),
                    json.dumps(merged_configuration),
                    now,
                    now,
                    existing.id,
                ),
            )
        return replace(
            existing,
            connection_id=connection_id,
            endpoint_id=endpoint_id,
            phone_number=phone_number,
            friendly_name=friendly_name,
            voice_url=voice_url,
            voice_method=voice_method,
            status=status,
            managed_mode=managed_mode,
            capabilities=merged_capabilities,
            configuration=merged_configuration,
            last_synced_at=now,
            updated_at=now,
        )

    def update_provider_number(
        self,
        number_id: str,
        *,
        friendly_name: str | None = None,
        voice_url: str | None = None,
        voice_method: str | None = None,
        status: str | None = None,
        endpoint_id: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> ProviderNumber | None:
        current = self.get_provider_number(number_id)
        if current is None:
            return None
        updated = replace(
            current,
            friendly_name=current.friendly_name if friendly_name is None else friendly_name,
            voice_url=current.voice_url if voice_url is None else voice_url,
            voice_method=current.voice_method if voice_method is None else voice_method,
            status=current.status if status is None else status,
            endpoint_id=current.endpoint_id if endpoint_id is None else endpoint_id,
            configuration=(
                current.configuration if configuration is None else configuration
            ),
            updated_at=_now(),
        )
        with self._connect() as db:
            db.execute(
                """
                UPDATE provider_numbers
                SET friendly_name = ?, voice_url = ?, voice_method = ?, status = ?,
                    endpoint_id = ?, configuration_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    updated.friendly_name,
                    updated.voice_url,
                    updated.voice_method,
                    updated.status,
                    updated.endpoint_id,
                    json.dumps(updated.configuration),
                    updated.updated_at,
                    updated.id,
                ),
            )
        return updated
