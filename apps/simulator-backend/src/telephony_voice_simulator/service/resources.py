"""Provider connections, endpoints (lines), the extension directory and the scenario catalog."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ..identifiers import is_directory_destination
from ..models import ProviderConnection
from ..storage.sqlite import StoreConflictError
from ..telephony import ProviderAdapter, ProviderError
from .errors import NotFoundError, ValidationError

_UNSET = object()


def _ring_timeout(value: int | str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError("Ring timeout must be a whole number")
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValidationError("Ring timeout must be a whole number") from exc
    if not 5 <= timeout <= 120:
        raise ValidationError("Ring timeout must be between 5 and 120 seconds")
    return timeout


class ResourcesMixin:
    def provider_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                **provider.descriptor.as_dict(),
                "runtime": provider.runtime_status(),
            }
            for provider in self.providers.values()
        ]

    def _validated_scenario(
        self,
        provider: ProviderAdapter,
        scenario_name: str,
    ) -> dict[str, Any]:
        scenario = self.catalog.get(scenario_name)
        if scenario is None:
            raise ValidationError(f"Unknown scenario: {scenario_name}")
        if not provider.supports_scenario(scenario):
            raise ValidationError(
                f"{provider.descriptor.name} does not support "
                f"{scenario.get('kind', 'this')} scenarios"
            )
        return scenario

    def _validate_endpoint(
        self,
        provider: ProviderAdapter,
        kind: str,
        address: str,
        *,
        endpoint_id: str | None = None,
    ) -> None:
        try:
            provider.validate_endpoint(
                self.store,
                kind=kind,
                address=address,
                endpoint_id=endpoint_id,
            )
        except ProviderError as exc:
            raise ValidationError(str(exc)) from exc

    def list_connections(self) -> list[dict[str, Any]]:
        return [self._connection_view(connection) for connection in self.store.list_connections()]

    def _connection_view(self, connection: ProviderConnection) -> dict[str, Any]:
        provider = self.providers.get(connection.provider)
        return {
            **connection.as_dict(),
            "runtime": provider.runtime_status(connection) if provider else {"ready": False},
        }

    def create_connection(
        self,
        provider_key: str,
        name: str,
        settings: dict[str, Any] | None = None,
        *,
        status: str = "ready",
        description: str = "",
    ) -> dict[str, Any]:
        provider = self.providers.get(provider_key)
        if provider is None:
            raise ValidationError(f"Unknown provider: {provider_key}")
        if not name.strip():
            raise ValidationError("Connection name is required")
        if status not in {"ready", "needs_setup", "disabled"}:
            raise ValidationError("Status must be ready, needs_setup, or disabled")
        candidate = ProviderConnection(
            id="", provider=provider_key, name=name.strip(), settings=settings or {},
            status=status, description=description.strip(), enabled=status != "disabled",
        )
        self._validate_connection(provider, candidate)
        connection = self.store.create_connection(
            provider_key,
            name.strip(),
            settings,
            status=status,
            description=description.strip(),
        )
        return self._connection_view(connection)

    @staticmethod
    def _validate_connection(provider: ProviderAdapter, connection: ProviderConnection) -> None:
        try:
            provider.validate_connection(connection)
        except ProviderError as exc:
            raise ValidationError(str(exc)) from exc

    def update_connection(
        self,
        connection_id: str,
        *,
        name: str | None = None,
        status: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = self.store.get_connection(connection_id)
        if current is None:
            raise NotFoundError(f"Unknown provider connection: {connection_id}")
        selected_name = current.name if name is None else name.strip()
        selected_status = current.status if status is None else status
        selected_description = (
            current.description if description is None else description.strip()
        )
        if not selected_name:
            raise ValidationError("Connection name is required")
        if selected_status not in {"ready", "needs_setup", "disabled"}:
            raise ValidationError("Status must be ready, needs_setup, or disabled")
        self._validate_connection(
            self.providers[current.provider],
            replace(
                current, name=selected_name, status=selected_status,
                description=selected_description,
                settings=current.settings if settings is None else settings,
                enabled=selected_status != "disabled",
            ),
        )
        updated = self.store.update_connection(
            connection_id,
            name=selected_name,
            status=selected_status,
            description=selected_description,
            settings=current.settings if settings is None else settings,
        )
        assert updated is not None
        if updated.status == "disabled":
            for endpoint in self.store.list_endpoints():
                if endpoint.connection_id == updated.id:
                    self.providers[current.provider].invalidate_pending_runs(
                        self.store,
                        endpoint,
                        "Provider connection was disabled before the queued PSTN call arrived",
                    )
        return self._connection_view(updated)

    def delete_connection(self, connection_id: str) -> None:
        current = self.store.get_connection(connection_id)
        if current is None:
            raise NotFoundError(f"Unknown provider connection: {connection_id}")
        for endpoint in self.store.list_endpoints():
            if endpoint.connection_id == connection_id:
                self.providers[current.provider].invalidate_pending_runs(
                    self.store,
                    endpoint,
                    "Provider connection was deleted before the queued PSTN call arrived",
                )
        if not self.store.delete_connection(connection_id):
            raise NotFoundError(f"Unknown provider connection: {connection_id}")

    def list_endpoints(self) -> list[dict[str, Any]]:
        connections = {item.id: item for item in self.store.list_connections()}
        return [
            {
                **endpoint.as_dict(),
                "provider": connections[endpoint.connection_id].provider,
                "connection_name": connections[endpoint.connection_id].name,
            }
            for endpoint in self.store.list_endpoints()
            if endpoint.connection_id in connections
        ]

    def create_endpoint(
        self,
        *,
        connection_id: str,
        name: str,
        kind: str,
        address: str,
        routing_mode: str = "fixed",
        default_scenario: str | None = None,
    ) -> dict[str, Any]:
        connection = self.store.get_connection(connection_id)
        if connection is None:
            raise ValidationError("Select a valid provider connection")
        provider = self.providers[connection.provider]
        if kind not in provider.descriptor.endpoint_kinds:
            raise ValidationError(f"{provider.descriptor.name} does not support {kind}")
        if routing_mode not in ("fixed", "queued"):
            raise ValidationError("Routing mode must be fixed or queued")
        if not name.strip() or not address.strip():
            raise ValidationError("Endpoint name and address are required")
        selected_address = address.strip()
        self._validate_endpoint(provider, kind, selected_address)
        if default_scenario:
            self._validated_scenario(provider, default_scenario)
        try:
            endpoint = self.store.create_endpoint(
                connection_id=connection_id,
                name=name.strip(),
                kind=kind,
                address=selected_address,
                routing_mode=routing_mode,
                default_scenario=default_scenario or None,
            )
        except StoreConflictError as exc:
            raise ValidationError(
                f"Endpoint address {selected_address} already exists for this connection"
            ) from exc
        provider.register_endpoint(self.store, endpoint)
        return {
            **endpoint.as_dict(),
            "provider": connection.provider,
            "connection_name": connection.name,
        }

    def update_endpoint(
        self,
        endpoint_id: str,
        *,
        connection_id: str | None = None,
        name: str | None = None,
        kind: str | None = None,
        address: str | None = None,
        routing_mode: str | None = None,
        default_scenario: str | None | object = _UNSET,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self.store.get_endpoint(endpoint_id)
        if current is None:
            raise NotFoundError(f"Unknown endpoint: {endpoint_id}")
        selected_connection = current.connection_id if connection_id is None else connection_id
        connection = self.store.get_connection(selected_connection)
        if connection is None:
            raise ValidationError("Select a valid provider connection")
        selected_kind = current.kind if kind is None else kind
        provider = self.providers[connection.provider]
        if selected_kind not in provider.descriptor.endpoint_kinds:
            raise ValidationError(f"{provider.descriptor.name} does not support {selected_kind}")
        selected_routing = current.routing_mode if routing_mode is None else routing_mode
        if selected_routing not in ("fixed", "queued"):
            raise ValidationError("Routing mode must be fixed or queued")
        selected_name = current.name if name is None else name.strip()
        selected_address = current.address if address is None else address.strip()
        if not selected_name or not selected_address:
            raise ValidationError("Endpoint name and address are required")
        self._validate_endpoint(
            provider,
            selected_kind,
            selected_address,
            endpoint_id=current.id,
        )
        selected_scenario = (
            current.default_scenario if default_scenario is _UNSET else default_scenario
        )
        if selected_scenario:
            self._validated_scenario(provider, str(selected_scenario))
        try:
            endpoint = self.store.update_endpoint(
                endpoint_id,
                connection_id=selected_connection,
                name=selected_name,
                kind=selected_kind,
                address=selected_address,
                routing_mode=selected_routing,
                default_scenario=str(selected_scenario) if selected_scenario else None,
                enabled=current.enabled if enabled is None else bool(enabled),
            )
        except StoreConflictError as exc:
            raise ValidationError(
                f"Endpoint address {selected_address} already exists for this connection"
            ) from exc
        assert endpoint is not None
        current_connection = self.store.get_connection(current.connection_id)
        provider.register_endpoint(self.store, endpoint)
        invalidates_queue = (
            current.address != endpoint.address
            or current.connection_id != endpoint.connection_id
            or current.routing_mode != endpoint.routing_mode
            or current.default_scenario != endpoint.default_scenario
            or (current.enabled and not endpoint.enabled)
        )
        if invalidates_queue and current_connection is not None:
            self.providers[current_connection.provider].invalidate_pending_runs(
                self.store,
                current,
                "Endpoint routing changed before the queued PSTN call arrived",
            )
        return {
            **endpoint.as_dict(),
            "provider": connection.provider,
            "connection_name": connection.name,
        }

    def delete_endpoint(self, endpoint_id: str) -> None:
        current = self.store.get_endpoint(endpoint_id)
        if current is None:
            raise NotFoundError(f"Unknown endpoint: {endpoint_id}")
        connection = self.store.get_connection(current.connection_id)
        if connection is not None:
            self.providers[connection.provider].invalidate_pending_runs(
                self.store,
                current,
                "Endpoint was deleted before the queued PSTN call arrived",
            )
        if not self.store.delete_endpoint(endpoint_id):
            raise NotFoundError(f"Unknown endpoint: {endpoint_id}")

    def list_directory_entries(self) -> list[dict[str, Any]]:
        connections = {item.id: item for item in self.store.list_connections()}
        return [
            {
                **entry.as_dict(),
                "provider": connections[entry.connection_id].provider,
                "connection_name": connections[entry.connection_id].name,
            }
            for entry in self.store.list_directory_entries()
            if entry.connection_id in connections
        ]

    def create_directory_entry(
        self,
        *,
        connection_id: str,
        extension: str,
        name: str,
        destination: str,
        department: str = "General",
        ring_timeout: int | str = 25,
    ) -> dict[str, Any]:
        connection = self.store.get_connection(connection_id)
        if connection is None:
            raise ValidationError("Select a valid provider connection")
        if not re.fullmatch(r"\d{4}", extension):
            raise ValidationError("Extension must contain exactly 4 digits")
        if not name.strip() or not destination.strip() or not department.strip():
            raise ValidationError("Name, destination, and department are required")
        if not is_directory_destination(destination.strip()):
            raise ValidationError(
                "Destination must be a canonical E.164 number or sip:/sips: URI"
            )
        timeout = _ring_timeout(ring_timeout)
        try:
            entry = self.store.create_directory_entry(
                connection_id=connection_id,
                extension=extension,
                name=name.strip(),
                destination=destination.strip(),
                department=department.strip(),
                ring_timeout=timeout,
            )
        except StoreConflictError as exc:
            raise ValidationError(f"Extension {extension} already exists") from exc
        return {
            **entry.as_dict(),
            "provider": connection.provider,
            "connection_name": connection.name,
        }

    def update_directory_entry(
        self,
        entry_id: str,
        *,
        connection_id: str | None = None,
        extension: str | None = None,
        name: str | None = None,
        destination: str | None = None,
        department: str | None = None,
        ring_timeout: int | str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self.store.get_directory_entry(entry_id)
        if current is None:
            raise NotFoundError(f"Unknown directory entry: {entry_id}")
        selected_connection = current.connection_id if connection_id is None else connection_id
        connection = self.store.get_connection(selected_connection)
        if connection is None:
            raise ValidationError("Select a valid provider connection")
        selected_extension = current.extension if extension is None else extension
        selected_name = current.name if name is None else name.strip()
        selected_destination = current.destination if destination is None else destination.strip()
        selected_department = current.department if department is None else department.strip()
        if not re.fullmatch(r"\d{4}", selected_extension):
            raise ValidationError("Extension must contain exactly 4 digits")
        if not selected_name or not selected_destination or not selected_department:
            raise ValidationError("Name, destination, and department are required")
        if not is_directory_destination(selected_destination):
            raise ValidationError(
                "Destination must be a canonical E.164 number or sip:/sips: URI"
            )
        timeout = current.ring_timeout if ring_timeout is None else _ring_timeout(ring_timeout)
        try:
            entry = self.store.update_directory_entry(
                entry_id,
                connection_id=selected_connection,
                extension=selected_extension,
                name=selected_name,
                destination=selected_destination,
                department=selected_department,
                ring_timeout=timeout,
                enabled=current.enabled if enabled is None else bool(enabled),
            )
        except StoreConflictError as exc:
            raise ValidationError(f"Extension {selected_extension} already exists") from exc
        assert entry is not None
        return {
            **entry.as_dict(),
            "provider": connection.provider,
            "connection_name": connection.name,
        }

    def delete_directory_entry(self, entry_id: str) -> None:
        if not self.store.delete_directory_entry(entry_id):
            raise NotFoundError(f"Unknown directory entry: {entry_id}")

    def list_scenarios(self) -> list[dict[str, Any]]:
        return self.catalog.list()

    def get_scenario(self, name: str) -> dict[str, Any]:
        scenario = self.catalog.get(name)
        if scenario is None:
            raise NotFoundError(f"Unknown scenario: {name}")
        return scenario