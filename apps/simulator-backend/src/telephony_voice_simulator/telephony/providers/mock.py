"""Credential-free telephony provider."""

from __future__ import annotations

from ..base import ProviderAdapter, RunDispatch, RunRequest
from ...control.models import ProviderCapabilities, ProviderDescriptor


class MockProvider(ProviderAdapter):
    descriptor = ProviderDescriptor(
        key="mock",
        name="Local simulator",
        description=(
            "Runs the same provider contract and scenario timeline locally without "
            "placing a carrier call."
        ),
        status="ready",
        capabilities=ProviderCapabilities(dtmf=True, recording=True, bridge=True, sip=True),
        endpoint_kinds=("phone_number", "sip_uri", "extension"),
    )

    async def dispatch(self, request: RunRequest) -> RunDispatch:
        main = next(
            (
                sequence["steps"]
                for sequence in request.scenario.get("sequences", [])
                if sequence["name"] == "main"
            ),
            [],
        )
        timeline = [{**step, "state": "simulated", "sequence": "main"} for step in main]
        return RunDispatch(
            status="completed",
            timeline=timeline,
            result={
                "graded": False,
                "mode": "dry_run",
                "summary": "Scenario timeline completed locally; no agent call was graded.",
            },
            completed=True,
        )
