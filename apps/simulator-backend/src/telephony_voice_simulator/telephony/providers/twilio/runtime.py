"""Runtime port between the Twilio adapter and the Twilio webhook engine."""

from __future__ import annotations

from typing import Protocol


class TwilioRuntime(Protocol):
    def scenario_available(self, scenario_name: str) -> bool: ...


class LocalTwilioRuntime:
    """Late-bound access to the in-process Twilio webhook runtime.

    Keeping this import behind a runtime port prevents the common control plane
    and the Telnyx module from importing Twilio webhook state.
    """

    def scenario_available(self, scenario_name: str) -> bool:
        from .webhooks import STATE

        return scenario_name in STATE.scenarios
