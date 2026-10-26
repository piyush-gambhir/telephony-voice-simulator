"""Controlled SMS inbox: read the stored messages and the receiver's configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..telephony.twilio import sms
from .errors import ValidationError


def list_messages(*, limit: int = 50) -> list[dict[str, Any]]:
    """Stored messages, newest first; empty when the inbox is unset or has no file yet."""
    if limit < 1:
        raise ValidationError("limit must be at least 1")
    return list(sms.list_messages(limit))


def status() -> dict[str, Any]:
    """Which numbers and senders the receiver accepts, and whether storage is configured.

    Reports booleans for credentials and never their values.
    """
    path = os.environ.get("PSTN_SMS_DB", "")
    return {
        "inbox_configured": bool(path),
        "inbox_exists": bool(path) and Path(path).is_file(),
        "numbers": sorted(sms.allowed_numbers()),
        "allowed_senders": sorted(sms.allowed_senders()),
        "account_configured": bool(os.environ.get("TWILIO_ACCOUNT_SID")),
        "signing_configured": bool(os.environ.get("TWILIO_AUTH_TOKEN")),
    }
