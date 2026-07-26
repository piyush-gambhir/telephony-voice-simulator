"""Compatibility alias for the Twilio provider webhook runtime."""

from __future__ import annotations

import sys

from ..telephony.providers.twilio import webhooks as _webhooks

if __name__ == "__main__":
    _webhooks.main()
else:
    # Preserve module-level state and monkeypatch behavior for downstream users
    # importing the historical telephony_voice_simulator.pstn.server path.
    sys.modules[__name__] = _webhooks
