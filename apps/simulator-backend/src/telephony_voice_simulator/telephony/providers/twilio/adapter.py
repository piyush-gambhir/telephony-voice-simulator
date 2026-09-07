"""Twilio provider adapter, isolated from the common control plane."""

from __future__ import annotations

import os

from ...base import (
    ProviderAdapter,
    ProviderError,
    ProviderRuntimeStore,
    RunDispatch,
    RunRequest,
)
from ....control.identifiers import is_canonical_e164, normalize_e164
from ....control.models import (
    Endpoint,
    ProviderCapabilities,
    ProviderConnection,
    ProviderDescriptor,
)
from ....pstn.config import is_absolute_https_url
from .runtime import TwilioRuntime


class TwilioProvider(ProviderAdapter):
    descriptor = ProviderDescriptor(
        key="twilio",
        name="Twilio",
        description="Queues scenarios on a Twilio phone number handled by the local PSTN server.",
        status="ready",
        capabilities=ProviderCapabilities(
            dtmf=True, recording=True, bridge=True, number_management=True, sip=False
        ),
        endpoint_kinds=("phone_number",),
        supported_scenario_kinds=("amd",),
    )

    def __init__(
        self,
        *,
        store: ProviderRuntimeStore,
        runtime: TwilioRuntime,
    ) -> None:
        self.store = store
        self.runtime = runtime

    def runtime_status(self, connection: ProviderConnection | None = None) -> dict[str, object]:
        credentials = bool(
            os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
        )
        public_url = is_absolute_https_url(os.environ.get("PUBLIC_BASE_URL", ""))
        return {
            "ready": credentials and public_url,
            "credentials": credentials,
            "public_url": public_url,
            "message": (
                "Ready"
                if credentials and public_url
                else "Set Twilio credentials and PUBLIC_BASE_URL before receiving PSTN calls."
            ),
        }

    def endpoint_identity(self, kind: str, address: str) -> str:
        return normalize_e164(address)

    def validate_endpoint(
        self,
        store: ProviderRuntimeStore,
        *,
        kind: str,
        address: str,
        endpoint_id: str | None = None,
    ) -> None:
        super().validate_endpoint(
            store,
            kind=kind,
            address=address,
            endpoint_id=endpoint_id,
        )
        if not is_canonical_e164(address):
            raise ProviderError(
                "Twilio endpoints must use a canonical E.164 phone number "
                "(for example +15550101000)"
            )
        identity = self.endpoint_identity(kind, address)
        for endpoint in store.list_endpoints():
            if endpoint.id == endpoint_id:
                continue
            connection = store.get_connection(endpoint.connection_id)
            if (
                connection is not None
                and connection.provider == self.descriptor.key
                and self.endpoint_identity(endpoint.kind, endpoint.address) == identity
            ):
                raise ProviderError(
                    f"Twilio number {address} is already managed by another endpoint"
                )

    def register_endpoint(
        self,
        store: ProviderRuntimeStore,
        endpoint: Endpoint,
    ) -> None:
        store.remember_provider_address(self.descriptor.key, endpoint.address)

    def invalidate_pending_runs(
        self,
        store: ProviderRuntimeStore,
        endpoint: Endpoint,
        reason: str,
    ) -> int:
        return store.fail_provider_queue(
            self.descriptor.key,
            endpoint.address,
            reason,
        )

    async def dispatch(self, request: RunRequest) -> RunDispatch:
        if not self.supports_scenario(request.scenario):
            raise ProviderError(
                "The Twilio PSTN callee runtime currently executes AMD scenarios only. "
                "Use the local simulator for IVR and PBX model scenarios."
            )
        if request.endpoint.kind != "phone_number":
            raise ProviderError("The current Twilio runtime queues scenarios by phone number.")
        scenario_name = str(request.scenario["name"])
        if not self.runtime.scenario_available(scenario_name):
            raise ProviderError(
                "The Twilio PSTN runtime is not active. Start the API with --with-pstn."
            )
        self.store.enqueue_provider_run(
            provider=self.descriptor.key,
            address=request.endpoint.address,
            scenario=scenario_name,
            run_id=request.run_id,
        )
        return RunDispatch(
            status="queued",
            result={
                "graded": False,
                "mode": "pstn",
                "summary": f"Next call to {request.endpoint.address} will run this scenario.",
            },
        )
