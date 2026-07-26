"""Application service shared by the CLI and HTTP API."""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..domains.ivr import Destination, simulate_directory_call
from ..paths import RESULTS_DIR
from ..telephony import ProviderAdapter, ProviderError, provider_registry
from ..telephony.base import RunRequest
from ..telephony.providers.twilio.management import (
    TwilioManagementError,
    TwilioNumberManager,
)
from .catalog import ScenarioCatalog
from .identifiers import is_directory_destination
from .models import ProviderConnection
from .store import SimulatorStore, StoreConflictError

_UNSET = object()


class NotFoundError(LookupError):
    pass


class ValidationError(ValueError):
    pass


class SimulatorService:
    def __init__(
        self,
        store: SimulatorStore | None = None,
        catalog: ScenarioCatalog | None = None,
        recordings_root: str | Path | None = None,
        twilio_numbers: TwilioNumberManager | None = None,
    ) -> None:
        self.store = store or SimulatorStore()
        self.catalog = catalog or ScenarioCatalog()
        self.providers = provider_registry(self.store)
        self.recordings_root = Path(recordings_root or RESULTS_DIR / "pstn").resolve()
        self.twilio_numbers = twilio_numbers or TwilioNumberManager()

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
        connection = self.store.create_connection(
            provider_key,
            name.strip(),
            settings,
            status=status,
            description=description.strip(),
        )
        provider.validate_connection(connection)
        return self._connection_view(connection)

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
        updated = self.store.update_connection(
            connection_id,
            name=selected_name,
            status=selected_status,
            description=selected_description,
            settings=current.settings if settings is None else settings,
        )
        assert updated is not None
        self.providers[current.provider].validate_connection(updated)
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
        except Exception as exc:
            if isinstance(exc, StoreConflictError) or "UNIQUE constraint failed" in str(exc):
                raise ValidationError(
                    f"Endpoint address {selected_address} already exists for this connection"
                ) from exc
            raise
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
        selected_connection = connection_id or current.connection_id
        connection = self.store.get_connection(selected_connection)
        if connection is None:
            raise ValidationError("Select a valid provider connection")
        selected_kind = kind or current.kind
        provider = self.providers[connection.provider]
        if selected_kind not in provider.descriptor.endpoint_kinds:
            raise ValidationError(f"{provider.descriptor.name} does not support {selected_kind}")
        selected_routing = routing_mode or current.routing_mode
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
        except Exception as exc:
            if isinstance(exc, StoreConflictError) or "UNIQUE constraint failed" in str(exc):
                raise ValidationError(
                    f"Endpoint address {selected_address} already exists for this connection"
                ) from exc
            raise
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
        try:
            timeout = int(ring_timeout)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Ring timeout must be a whole number") from exc
        if not 5 <= timeout <= 120:
            raise ValidationError("Ring timeout must be between 5 and 120 seconds")
        try:
            entry = self.store.create_directory_entry(
                connection_id=connection_id,
                extension=extension,
                name=name.strip(),
                destination=destination.strip(),
                department=department.strip(),
                ring_timeout=timeout,
            )
        except Exception as exc:
            if isinstance(exc, StoreConflictError) or "UNIQUE constraint failed" in str(exc):
                raise ValidationError(f"Extension {extension} already exists") from exc
            raise
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
        selected_connection = connection_id or current.connection_id
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
        try:
            timeout = current.ring_timeout if ring_timeout is None else int(ring_timeout)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Ring timeout must be a whole number") from exc
        if not 5 <= timeout <= 120:
            raise ValidationError("Ring timeout must be between 5 and 120 seconds")
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
        except Exception as exc:
            if isinstance(exc, StoreConflictError) or "UNIQUE constraint failed" in str(exc):
                raise ValidationError(f"Extension {selected_extension} already exists") from exc
            raise
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

    def list_runs(self) -> list[dict[str, Any]]:
        return [run.as_dict() for run in self.store.list_runs()]

    def list_calls(self) -> list[dict[str, Any]]:
        return [call.as_dict() for call in self.store.list_incoming_calls()]

    @staticmethod
    def _is_hosted_amd_number(remote: dict[str, Any]) -> bool:
        voice_url = str(remote.get("voice_url") or "")
        parsed = urlparse(voice_url)
        query = parse_qs(parsed.query)
        return parsed.path.rstrip("/") == "/machine" and query.get("mode") == ["voice"]

    def _amd_connection(self):
        connection = next(
            (
                item
                for item in self.store.list_connections()
                if item.provider == "twilio"
                and item.settings.get("managed_mode") == "amd"
            ),
            None,
        )
        if connection is not None:
            return connection
        return self.store.create_connection(
            "twilio",
            "Twilio AMD simulator",
            {
                "managed_mode": "amd",
                "source": "twilio_inventory",
            },
            description="Four Twilio numbers dedicated to AMD scenario testing.",
        )

    def _amd_number_view(self, number) -> dict[str, Any]:
        endpoint = (
            self.store.get_endpoint(number.endpoint_id)
            if number.endpoint_id
            else None
        )
        public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        target_voice_url = (
            f"{public_base_url}/twilio/voice"
            if public_base_url.startswith("https://")
            else None
        )
        pending = (
            self.store.list_provider_queue("twilio", endpoint.address)
            if endpoint is not None
            else []
        )
        return {
            **number.as_dict(),
            "endpoint": endpoint.as_dict() if endpoint is not None else None,
            "target_voice_url": target_voice_url,
            "attached_to_runtime": bool(
                target_voice_url and number.voice_url == target_voice_url
            ),
            "pending_runs": len(pending),
        }

    def list_amd_numbers(self) -> list[dict[str, Any]]:
        return [
            self._amd_number_view(number)
            for number in self.store.list_provider_numbers(
                provider="twilio",
                managed_mode="amd",
            )
        ]

    async def sync_amd_numbers(self) -> list[dict[str, Any]]:
        try:
            remote_numbers = await self.twilio_numbers.list_numbers()
        except TwilioManagementError as exc:
            raise ValidationError(str(exc)) from exc
        known_resources = {
            item.provider_resource_id
            for item in self.store.list_provider_numbers(
                provider="twilio",
                managed_mode="amd",
            )
        }
        selected = [
            item
            for item in remote_numbers
            if str(item.get("sid") or "") in known_resources
            or self._is_hosted_amd_number(item)
        ]
        if not selected:
            raise ValidationError(
                "No Twilio numbers are currently attached to an AMD /machine?mode=voice URL."
            )
        connection = self._amd_connection()
        endpoints = self.store.list_endpoints()
        for remote in selected:
            phone_number = str(remote.get("phone_number") or "").strip()
            resource_id = str(remote.get("sid") or "").strip()
            friendly_name = str(remote.get("friendly_name") or phone_number).strip()
            if not phone_number or not resource_id:
                continue
            endpoint = next(
                (
                    item
                    for item in endpoints
                    if item.connection_id == connection.id and item.address == phone_number
                ),
                None,
            )
            if endpoint is None:
                endpoint = self.store.create_endpoint(
                    connection_id=connection.id,
                    name=friendly_name,
                    kind="phone_number",
                    address=phone_number,
                    routing_mode="queued",
                    default_scenario="stock_voicemail_beep_1000",
                )
                self.providers["twilio"].register_endpoint(self.store, endpoint)
                endpoints.append(endpoint)
            self.store.upsert_provider_number(
                provider="twilio",
                connection_id=connection.id,
                endpoint_id=endpoint.id,
                provider_resource_id=resource_id,
                phone_number=phone_number,
                friendly_name=friendly_name,
                voice_url=str(remote.get("voice_url") or ""),
                voice_method=str(remote.get("voice_method") or "POST"),
                status="active",
                managed_mode="amd",
                capabilities=(
                    remote.get("capabilities")
                    if isinstance(remote.get("capabilities"), dict)
                    else {}
                ),
            )
        return self.list_amd_numbers()

    async def update_amd_number(
        self,
        number_id: str,
        *,
        friendly_name: str | None = None,
        default_scenario: str | None | object = _UNSET,
        routing_mode: str | None = None,
        enabled: bool | None = None,
        record_full_calls: bool | None = None,
    ) -> dict[str, Any]:
        number = self.store.get_provider_number(number_id)
        if number is None or number.provider != "twilio" or number.managed_mode != "amd":
            raise NotFoundError(f"Unknown AMD number: {number_id}")
        endpoint = (
            self.store.get_endpoint(number.endpoint_id)
            if number.endpoint_id
            else None
        )
        if endpoint is None:
            raise ValidationError("The AMD number is not linked to an endpoint.")
        selected_name = number.friendly_name
        if friendly_name is not None:
            selected_name = friendly_name.strip()
            if not selected_name or len(selected_name) > 64:
                raise ValidationError("Twilio number name must contain 1 to 64 characters.")
            if selected_name != number.friendly_name:
                try:
                    remote = await self.twilio_numbers.update_number(
                        number.provider_resource_id,
                        friendly_name=selected_name,
                    )
                except TwilioManagementError as exc:
                    raise ValidationError(str(exc)) from exc
                selected_name = str(remote.get("friendly_name") or selected_name)
                number = self.store.update_provider_number(
                    number.id,
                    friendly_name=selected_name,
                ) or number
        self.update_endpoint(
            endpoint.id,
            name=selected_name,
            routing_mode=routing_mode,
            default_scenario=default_scenario,
            enabled=enabled,
        )
        if record_full_calls is not None:
            if not isinstance(record_full_calls, bool):
                raise ValidationError("Full-call recording must be true or false.")
            configuration = {
                **number.configuration,
                "record_full_calls": record_full_calls,
            }
            number = self.store.update_provider_number(
                number.id,
                configuration=configuration,
            ) or number
        updated = self.store.get_provider_number(number.id)
        assert updated is not None
        return self._amd_number_view(updated)

    async def attach_amd_number(self, number_id: str) -> dict[str, Any]:
        number = self.store.get_provider_number(number_id)
        if number is None or number.provider != "twilio" or number.managed_mode != "amd":
            raise NotFoundError(f"Unknown AMD number: {number_id}")
        public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        if not public_base_url.startswith("https://"):
            raise ValidationError(
                "Start the HTTPS public ingress and set PUBLIC_BASE_URL before attaching numbers."
            )
        target = f"{public_base_url}/twilio/voice"
        configuration = dict(number.configuration)
        if number.voice_url != target:
            configuration.setdefault("previous_voice_url", number.voice_url)
            configuration.setdefault("previous_voice_method", number.voice_method)
        try:
            remote = await self.twilio_numbers.update_number(
                number.provider_resource_id,
                voice_url=target,
                voice_method="POST",
            )
        except TwilioManagementError as exc:
            raise ValidationError(str(exc)) from exc
        updated = self.store.update_provider_number(
            number.id,
            voice_url=str(remote.get("voice_url") or target),
            voice_method=str(remote.get("voice_method") or "POST"),
            configuration=configuration,
        )
        assert updated is not None
        return self._amd_number_view(updated)

    async def restore_amd_number(self, number_id: str) -> dict[str, Any]:
        number = self.store.get_provider_number(number_id)
        if number is None or number.provider != "twilio" or number.managed_mode != "amd":
            raise NotFoundError(f"Unknown AMD number: {number_id}")
        previous_url = str(number.configuration.get("previous_voice_url") or "")
        if not previous_url:
            raise ValidationError("No previous Twilio Voice URL is stored for this number.")
        previous_method = str(
            number.configuration.get("previous_voice_method") or "POST"
        )
        try:
            remote = await self.twilio_numbers.update_number(
                number.provider_resource_id,
                voice_url=previous_url,
                voice_method=previous_method,
            )
        except TwilioManagementError as exc:
            raise ValidationError(str(exc)) from exc
        configuration = dict(number.configuration)
        configuration.pop("previous_voice_url", None)
        configuration.pop("previous_voice_method", None)
        updated = self.store.update_provider_number(
            number.id,
            voice_url=str(remote.get("voice_url") or previous_url),
            voice_method=str(remote.get("voice_method") or previous_method),
            configuration=configuration,
        )
        assert updated is not None
        return self._amd_number_view(updated)

    async def queue_amd_number(
        self,
        number_id: str,
        scenario: str,
    ) -> dict[str, Any]:
        number = self.store.get_provider_number(number_id)
        if number is None or number.provider != "twilio" or number.managed_mode != "amd":
            raise NotFoundError(f"Unknown AMD number: {number_id}")
        if not number.endpoint_id:
            raise ValidationError("The AMD number is not linked to an endpoint.")
        endpoint = self.store.get_endpoint(number.endpoint_id)
        if endpoint is None or endpoint.routing_mode != "queued":
            raise ValidationError(
                "Set call selection to 'Queue, then default' before queueing a scenario."
            )
        run = await self.create_run(number.endpoint_id, scenario)
        updated = self.store.get_provider_number(number.id)
        assert updated is not None
        return {"number": self._amd_number_view(updated), "run": run}

    def _owned_call_directory(self, call_id: str) -> Path | None:
        """Resolve the direct PSTN result directory for a non-traversing call ID."""

        if not call_id or Path(call_id).name != call_id or call_id in {".", ".."}:
            return None
        candidate = self.recordings_root / call_id
        try:
            if candidate.is_symlink():
                return None
            resolved = candidate.resolve()
        except OSError:
            return None
        return resolved if resolved.parent == self.recordings_root else None

    def _owned_recording_path(self, call_id: str, local_path: str) -> Path | None:
        call_directory = self._owned_call_directory(call_id)
        if call_directory is None:
            return None
        try:
            resolved = Path(local_path).resolve()
        except OSError:
            return None
        if resolved == call_directory or not resolved.is_relative_to(call_directory):
            return None
        return resolved

    def delete_call(self, call_id: str) -> dict[str, Any]:
        """Erase persisted call metadata and simulator-owned recording files.

        Only the direct, non-symlinked result directory for this call can be
        removed. External files are deliberately left untouched even if a
        database row happens to reference them.
        """

        call = self.store.get_incoming_call(call_id)
        already_deleted = call is None and self.store.is_incoming_call_deleted(call_id)
        if call is None and not already_deleted:
            raise NotFoundError(f"Unknown incoming call: {call_id}")

        owned_recording_files: set[Path] = set()
        missing_files = 0
        skipped_unowned_files = 0
        external_provider_recordings = []
        if call is not None:
            external_provider_recordings = [
                recording
                for recording in call.recordings
                if recording.provider not in {"", "local", "mock"}
            ]
            for recording in call.recordings:
                if not recording.local_path:
                    continue
                resolved = self._owned_recording_path(call.id, recording.local_path)
                if resolved is None:
                    skipped_unowned_files += 1
                    continue
                if not resolved.is_file():
                    missing_files += 1
                    continue
                owned_recording_files.add(resolved)

            # Persist the tombstone and remove call/recording rows before
            # touching files. Late callbacks in this or another process now
            # fail closed, and a cleanup failure can be retried idempotently.
            if not self.store.delete_incoming_call(call_id):
                raise NotFoundError(f"Unknown incoming call: {call_id}")

        call_directory = self._owned_call_directory(call_id)
        artifacts_directory_deleted = False
        if call_directory is not None and call_directory.is_dir():
            try:
                shutil.rmtree(call_directory)
            except OSError as exc:
                raise ValidationError(
                    "Could not remove the simulator-owned call artifacts; "
                    "metadata is tombstoned and artifact cleanup can be retried"
                ) from exc
            artifacts_directory_deleted = True

        return {
            "deleted": call_id,
            "already_deleted": already_deleted,
            "recording_files": {
                "deleted": len(owned_recording_files),
                "missing": missing_files,
                "skipped_unowned": skipped_unowned_files,
            },
            "external_provider_recordings": {
                "retained": len(external_provider_recordings),
                "providers": sorted(
                    {recording.provider for recording in external_provider_recordings}
                ),
                "note": (
                    "This deletion removes simulator metadata and local artifacts only. "
                    "Provider-hosted recordings follow provider retention and must be "
                    "deleted through the provider separately."
                ),
            },
            "artifacts_directory_deleted": artifacts_directory_deleted,
        }

    def recording_path(self, recording_id: str) -> Path:
        recording = self.store.get_recording(recording_id)
        if recording is None or not recording.local_path:
            raise NotFoundError(f"Recording is not available locally: {recording_id}")
        path = self._owned_recording_path(recording.call_id, recording.local_path)
        if path is None or not path.is_file():
            raise NotFoundError(f"Recording file is missing: {recording_id}")
        return path

    async def create_run(
        self, endpoint_id: str, scenario_name: str | None = None
    ) -> dict[str, Any]:
        endpoint = self.store.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValidationError("Select a valid endpoint")
        if not endpoint.enabled:
            raise ValidationError("The selected endpoint is disabled")
        connection = self.store.get_connection(endpoint.connection_id)
        if connection is None or not connection.enabled:
            raise ValidationError("The endpoint provider connection is unavailable")
        # A fixed endpoint models a number permanently wired to one behavior:
        # when a default exists, ad-hoc run requests cannot override it. Queued
        # endpoints accept a per-run scenario and fall back to their default.
        selected = (
            endpoint.default_scenario
            if endpoint.routing_mode == "fixed" and endpoint.default_scenario
            else scenario_name or endpoint.default_scenario
        )
        if not selected:
            raise ValidationError("Select a scenario or configure a default on the endpoint")
        scenario = self.catalog.get(selected)
        if scenario is None:
            raise ValidationError(f"Unknown scenario: {selected}")
        provider = self.providers[connection.provider]
        scenario = self._validated_scenario(provider, selected)
        run = self.store.create_run(endpoint.id, connection.provider, selected)
        try:
            dispatched = await provider.dispatch(
                RunRequest(
                    run_id=run.id,
                    connection=connection,
                    endpoint=endpoint,
                    scenario=scenario,
                )
            )
        except ProviderError as exc:
            failed = self.store.update_run(
                run,
                status="failed",
                result={"graded": False, "error": str(exc)},
                completed=True,
            )
            return failed.as_dict()
        updated = self.store.update_run(
            run,
            status=dispatched.status,
            timeline=dispatched.timeline,
            result=dispatched.result,
            completed=dispatched.completed,
        )
        return updated.as_dict()

    def create_ivr_simulation(
        self,
        *,
        endpoint_id: str,
        caller_number: str,
        extension: str,
        disposition: str,
    ) -> dict[str, Any]:
        endpoint = self.store.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValidationError("Select a valid public endpoint")
        if not endpoint.enabled:
            raise ValidationError("The selected public endpoint is disabled")
        if not 5 <= len(caller_number.strip()) <= 40:
            raise ValidationError("Caller number must contain 5 to 40 characters")
        if not re.fullmatch(r"\d{4}", extension):
            raise ValidationError("Extension must contain exactly 4 digits")
        allowed = {"connected", "busy", "no_answer", "failed", "abandoned"}
        if disposition not in allowed:
            raise ValidationError(
                "Disposition must be connected, busy, no_answer, failed, or abandoned"
            )

        connection = self.store.get_connection(endpoint.connection_id)
        if connection is None or not connection.enabled:
            raise ValidationError("The endpoint provider connection is unavailable")
        entry = self.store.get_directory_entry_by_extension(extension)
        route_connection = (
            self.store.get_connection(entry.connection_id) if entry is not None else None
        )
        destination = (
            Destination(entry.name, entry.destination)
            if entry is not None
            and entry.enabled
            and route_connection is not None
            and route_connection.enabled
            else None
        )
        steps, outcome = simulate_directory_call(
            caller_number=caller_number.strip(),
            public_number=endpoint.address,
            extension=extension,
            destination=destination,
            ring_timeout=entry.ring_timeout if entry else 25,
            forced_disposition=disposition,
        )
        call_outcome = {
            "bridged": "connected",
            "dial_busy": "busy",
            "dial_no_answer": "no_answer",
            "dial_failed": "failed",
            "failed": "failed",
            "abandoned": "abandoned",
        }[outcome]
        duration_seconds = (
            142
            if call_outcome == "connected"
            else entry.ring_timeout
            if call_outcome == "no_answer" and entry
            else 8
            if call_outcome == "busy"
            else 4
        )
        run = self.store.create_run(
            endpoint.id,
            route_connection.provider if destination and route_connection else connection.provider,
            "ivr_custom",
            caller_number=caller_number.strip(),
            extension=extension,
            destination=entry.destination if destination and entry else None,
            outcome=call_outcome,
            duration_seconds=duration_seconds,
        )
        completed = self.store.update_run(
            run,
            status="completed",
            timeline=[
                {**step.as_dict(index), "state": "simulated", "sequence": "main"}
                for index, step in enumerate(steps)
            ],
            result={
                "graded": False,
                "mode": "ivr_directory",
                "summary": f"Directory simulation completed with outcome: {outcome}.",
                "outcome": outcome,
                "call_outcome": call_outcome,
                "caller_number": caller_number.strip(),
                "extension": extension,
                "destination": entry.destination if destination and entry else None,
                "duration_seconds": duration_seconds,
            },
            completed=True,
        )
        return completed.as_dict()
