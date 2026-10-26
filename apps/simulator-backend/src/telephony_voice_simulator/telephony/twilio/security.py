"""Twilio request signatures and CallSid validation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from pathlib import Path

from aiohttp import web

from . import state
from .twiml import public_url

_TWILIO_CALL_SID = re.compile(r"CA[a-fA-F0-9]{32}")
_LOCAL_CALL_SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")


def call_sid_is_valid(call_id: str) -> bool:
    """Validate carrier IDs before any cache, database, or filesystem access."""

    if os.environ.get("TWILIO_AUTH_TOKEN", ""):
        return _TWILIO_CALL_SID.fullmatch(call_id) is not None
    return (
        os.environ.get("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "").lower()
        in {"1", "true", "yes"}
        and _LOCAL_CALL_SID.fullmatch(call_id) is not None
        and call_id not in {".", ".."}
    )


def reject_invalid_call_sid(call_id: str) -> web.Response | None:
    if call_sid_is_valid(call_id):
        return None
    return web.json_response({"error": "invalid Twilio CallSid"}, status=400)


def call_results_dir(call_id: str) -> Path:
    """Return the confined direct result directory for a validated call ID."""

    if _LOCAL_CALL_SID.fullmatch(call_id) is None or call_id in {".", ".."}:
        raise ValueError("invalid Twilio CallSid")
    root = state.PSTN_RESULTS_DIR.resolve()
    candidate = (root / call_id).resolve()
    if candidate.parent != root:
        raise ValueError("invalid Twilio CallSid")
    return candidate


def request_is_valid(request: web.Request, form: object) -> bool:
    """Validate Twilio's HMAC signature for a webhook request.

    Local unsigned calls require an explicit opt-in. Production receives the
    auth token as part of the normal Twilio configuration.
    """
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not token:
        return os.environ.get("PSTN_ALLOW_UNSIGNED_WEBHOOKS", "").lower() in {
            "1",
            "true",
            "yes",
        }
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature or not hasattr(form, "keys") or not hasattr(form, "getall"):
        return False
    payload = public_url(str(request.rel_url))
    # Twilio sorts both field names and repeated values before signing.
    for key in sorted(set(form.keys())):
        for value in sorted(set(form.getall(key))):
            payload += str(key) + str(value)
    expected = base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(signature, expected)


def reject_invalid_request(request: web.Request, form: object) -> web.Response | None:
    if request_is_valid(request, form):
        return None
    return web.json_response({"error": "invalid Twilio signature"}, status=403)
