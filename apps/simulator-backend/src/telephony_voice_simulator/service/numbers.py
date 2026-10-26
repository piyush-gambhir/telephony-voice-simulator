"""Managed Twilio numbers: sync, configure, attach to the AMD runtime, restore, queue."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..telephony.twilio.management import (
    TwilioManagementError,
)
from .errors import NotFoundError, ValidationError

_UNSET = object()


class NumbersMixin:
    @staticmethod
    def _is_hosted_amd_number(remote: dict[str, Any]) -> bool:
        """Does this Twilio number already answer with an AMD callee runtime?

        Two shapes count. ``/machine?mode=voice`` is the optional Twilio
        Serverless deployment. ``/twilio/voice`` is the local runtime, and is
        exactly the ``target_voice_url`` this service attaches numbers to —
        without it, a fresh database can never adopt a number, because
        discovery would only ever match the Serverless deployment.
        """
        voice_url = str(remote.get("voice_url") or "")
        parsed = urlparse(voice_url)
        path = parsed.path.rstrip("/")
        if path == "/twilio/voice":
            return True
        query = parse_qs(parsed.query)
        return path == "/machine" and query.get("mode") == ["voice"]

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
        # Validate the whole update before changing anything at the carrier.
        if routing_mode is not None and routing_mode not in {"fixed", "queued"}:
            raise ValidationError("Routing mode must be fixed or queued")
        for label, value in (("enabled", enabled), ("record_full_calls", record_full_calls)):
            if value is not None and not isinstance(value, bool):
                raise ValidationError(f"{label} must be a boolean")
        if default_scenario is not _UNSET and default_scenario is not None:
            if not isinstance(default_scenario, str):
                raise ValidationError("default_scenario must be a string or null")
            if default_scenario:
                self._validated_scenario(self.providers[number.provider], default_scenario)
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
            if "previous_status_callback" not in configuration:
                inventory = await self.twilio_numbers.list_numbers()
                current = next(
                    (item for item in inventory if item.get("sid") == number.provider_resource_id),
                    None,
                )
                if current is None:
                    raise ValidationError("Twilio number is missing from the account inventory.")
                configuration["previous_status_callback"] = str(current.get("status_callback") or "")
                configuration["previous_status_callback_method"] = str(
                    current.get("status_callback_method") or "POST"
                )
            remote = await self.twilio_numbers.update_number(
                number.provider_resource_id,
                voice_url=target,
                voice_method="POST",
                status_callback=f"{public_base_url}/twilio/status",
                status_callback_method="POST",
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
                status_callback=number.configuration.get("previous_status_callback"),
                status_callback_method=str(
                    number.configuration.get("previous_status_callback_method") or "POST"
                ),
            )
        except TwilioManagementError as exc:
            raise ValidationError(str(exc)) from exc
        configuration = dict(number.configuration)
        configuration.pop("previous_voice_url", None)
        configuration.pop("previous_voice_method", None)
        configuration.pop("previous_status_callback", None)
        configuration.pop("previous_status_callback_method", None)
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
