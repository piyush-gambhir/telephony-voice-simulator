"""Account number inventory: list, point at a simulator route, restore.

These act on the live Twilio account. Every mutation names one explicit number,
point writes a JSON backup of the number's previous configuration before changing it,
and restore refuses a backup that belongs to another number.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ..paths import RESULTS_DIR
from ..telephony.twilio.management import TwilioManagementError, TwilioNumberManager
from .errors import ConflictError, NotFoundError, ValidationError

ROUTES = {"ivr": "/twilio/ivr", "amd": "/twilio/voice"}
LINE_NAME = "telephony-voice-simulator-line"
_BACKUP_KEYS = (
    "sid",
    "phone_number",
    "friendly_name",
    "voice_url",
    "voice_method",
    "status_callback",
    "status_callback_method",
)


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "sid": record.get("sid"),
        "phone_number": record.get("phone_number"),
        "friendly_name": record.get("friendly_name") or "",
        "voice_url": record.get("voice_url") or "",
        "voice_method": record.get("voice_method"),
        "status_callback": record.get("status_callback") or "",
    }


def _snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "saved_at": dt.datetime.now(dt.UTC).isoformat(),
        **{key: record.get(key) for key in _BACKUP_KEYS},
    }


async def _find(manager: TwilioNumberManager, number: str) -> dict[str, Any]:
    if not number:
        raise ValidationError("a phone number is required")
    try:
        return await manager.find_number(number)
    except TwilioManagementError as exc:
        raise ConflictError(str(exc)) from exc


async def _update(manager: TwilioNumberManager, sid: str, **changes: Any) -> None:
    try:
        await manager.update_number(sid, **changes)
    except TwilioManagementError as exc:
        raise ConflictError(str(exc)) from exc


async def list_account_numbers(
    *, manager: TwilioNumberManager | None = None
) -> list[dict[str, Any]]:
    """Every number on the Twilio account with its voice and status webhooks."""
    try:
        records = await (manager or TwilioNumberManager()).list_numbers()
    except TwilioManagementError as exc:
        raise ConflictError(str(exc)) from exc
    return [_summary(record) for record in records]


async def point_number(
    *,
    number: str,
    route: str = "ivr",
    base_url: str = "",
    voice_url: str | None = None,
    status_callback_url: str | None = None,
    backup: Path | None = None,
    dry_run: bool = False,
    manager: TwilioNumberManager | None = None,
) -> dict[str, Any]:
    """Point one number at a simulator route or explicit URL, saving a backup first.

    Returns the planned change, the backup snapshot and where it is written, and
    ``applied`` (False for a dry run, which writes and changes nothing).
    """
    if route not in ROUTES:
        raise ValidationError(f"route must be one of {', '.join(ROUTES)}")
    base = (base_url or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/")
    url = voice_url or (urljoin(f"{base}/", ROUTES[route].lstrip("/")) if base else "")
    if not url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
        raise ValidationError("provide an HTTPS --base-url or --voice-url")
    manager = manager or TwilioNumberManager()
    record = await _find(manager, number)
    change: dict[str, str] = {
        "VoiceUrl": url,
        "VoiceMethod": "POST",
        "FriendlyName": LINE_NAME,
    }
    if status_callback_url:
        change["StatusCallback"] = status_callback_url
        change["StatusCallbackMethod"] = "POST"
    backup_path = backup or RESULTS_DIR / "twilio" / f"{record['sid']}.json"
    snapshot = _snapshot(record)
    result: dict[str, Any] = {
        "number": number,
        "sid": record["sid"],
        "change": change,
        "backup": snapshot,
        "backup_path": str(backup_path),
        "applied": False,
    }
    if dry_run:
        return result
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(snapshot, indent=2) + "\n")
    await _update(
        manager,
        record["sid"],
        friendly_name=LINE_NAME,
        voice_url=url,
        status_callback=status_callback_url or None,
    )
    result["applied"] = True
    return result


async def restore_number(
    *,
    number: str,
    backup: Path,
    dry_run: bool = False,
    manager: TwilioNumberManager | None = None,
) -> dict[str, Any]:
    """Put back the configuration saved by ``point_number``; refuses another number's backup."""
    manager = manager or TwilioNumberManager()
    record = await _find(manager, number)
    try:
        snapshot = json.loads(backup.read_text())
    except FileNotFoundError as exc:
        raise NotFoundError(f"backup not found: {backup}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"backup is unreadable: {exc}") from exc
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("sid") != record["sid"]
        or snapshot.get("phone_number") != number
    ):
        raise ValidationError("backup does not belong to the selected number")
    restore = {
        "VoiceUrl": snapshot.get("voice_url") or "",
        "VoiceMethod": snapshot.get("voice_method") or "POST",
        "StatusCallback": snapshot.get("status_callback") or "",
        "StatusCallbackMethod": snapshot.get("status_callback_method") or "POST",
        "FriendlyName": snapshot.get("friendly_name") or "restored-number",
    }
    result: dict[str, Any] = {
        "number": number,
        "sid": record["sid"],
        "restore": restore,
        "applied": False,
    }
    if dry_run:
        return result
    await _update(
        manager,
        record["sid"],
        friendly_name=restore["FriendlyName"],
        voice_url=restore["VoiceUrl"],
        voice_method=restore["VoiceMethod"],
        status_callback=restore["StatusCallback"],
        status_callback_method=restore["StatusCallbackMethod"],
    )
    result["applied"] = True
    return result
