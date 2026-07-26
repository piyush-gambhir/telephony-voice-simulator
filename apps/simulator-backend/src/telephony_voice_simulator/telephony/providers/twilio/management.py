"""Twilio account operations used by the private control plane.

Credentials stay in the environment. Only normalized number metadata is
returned to the service layer and persisted by the repository.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class TwilioManagementError(RuntimeError):
    """A Twilio management request failed."""


class TwilioNumberManager:
    api = "https://api.twilio.com/2010-04-01"

    def _credentials(self) -> tuple[str, str]:
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        if not account_sid or not auth_token:
            raise TwilioManagementError(
                "Twilio credentials are not configured in the backend environment."
            )
        return account_sid, auth_token

    async def list_numbers(self) -> list[dict[str, Any]]:
        account_sid, auth_token = self._credentials()
        next_url: str | None = (
            f"{self.api}/Accounts/{account_sid}/IncomingPhoneNumbers.json"
        )
        parameters: dict[str, object] | None = {"PageSize": 1000}
        records: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            auth=(account_sid, auth_token),
            timeout=60,
        ) as client:
            while next_url:
                response = await client.get(next_url, params=parameters)
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise TwilioManagementError(
                        f"Twilio number inventory request failed ({response.status_code})."
                    ) from exc
                payload = response.json()
                records.extend(payload.get("incoming_phone_numbers", []))
                next_page = payload.get("next_page_uri")
                next_url = (
                    f"https://api.twilio.com{next_page}"
                    if isinstance(next_page, str) and next_page
                    else None
                )
                parameters = None
        return records

    async def update_number(
        self,
        provider_resource_id: str,
        *,
        friendly_name: str | None = None,
        voice_url: str | None = None,
        voice_method: str = "POST",
    ) -> dict[str, Any]:
        account_sid, auth_token = self._credentials()
        data: dict[str, str] = {}
        if friendly_name is not None:
            data["FriendlyName"] = friendly_name
        if voice_url is not None:
            data["VoiceUrl"] = voice_url
            data["VoiceMethod"] = voice_method
        if not data:
            raise TwilioManagementError("No Twilio number changes were requested.")
        async with httpx.AsyncClient(
            auth=(account_sid, auth_token),
            timeout=60,
        ) as client:
            response = await client.post(
                (
                    f"{self.api}/Accounts/{account_sid}/IncomingPhoneNumbers/"
                    f"{provider_resource_id}.json"
                ),
                data=data,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TwilioManagementError(
                f"Twilio number update failed ({response.status_code})."
            ) from exc
        return response.json()
