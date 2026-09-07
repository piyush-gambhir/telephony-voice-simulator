"""Independent Telnyx provider module.

The adapter exposes its full boundary now. Live Call Control dispatch can be
implemented here without changing the control service or Twilio module.
"""

from __future__ import annotations

import os

from ..base import (
    ProviderAdapter,
    ProviderError,
    ProviderRuntimeStore,
    RunDispatch,
    RunRequest,
)
from ...control.identifiers import is_canonical_e164, is_directory_destination
from ...control.models import (
    ProviderCapabilities,
    ProviderConnection,
    ProviderDescriptor,
)


class TelnyxProvider(ProviderAdapter):
    descriptor = ProviderDescriptor(
        key="telnyx",
        name="Telnyx",
        description="Telnyx Call Control adapter boundary; execution is not implemented yet.",
        status="preview",
        capabilities=ProviderCapabilities(
            dtmf=True, recording=True, bridge=True, number_management=True, sip=True
        ),
        endpoint_kinds=("phone_number", "sip_uri"),
        supported_scenario_kinds=(),
    )

    def runtime_status(self, connection: ProviderConnection | None = None) -> dict[str, object]:
        configured = bool(os.environ.get("TELNYX_API_KEY"))
        return {
            "ready": False,
            "credentials": configured,
            "message": "Adapter contract is installed; Call Control execution is the next milestone.",
        }

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
        if kind == "phone_number" and not is_canonical_e164(address):
            raise ProviderError(
                "Telnyx phone endpoints must use canonical E.164 "
                "(for example +15550101000)"
            )
        if kind == "sip_uri" and not (
            address.lower().startswith(("sip:", "sips:"))
            and is_directory_destination(address)
        ):
            raise ProviderError("Telnyx SIP endpoints must use a valid sip: or sips: URI")

    async def dispatch(self, request: RunRequest) -> RunDispatch:
        raise ProviderError("Telnyx execution is not implemented yet.")
