"""Start full-call audit recordings and download finished recordings."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re

from aiohttp import ClientSession, ClientTimeout, encode_basic_auth

from .security import call_results_dir
from .state import call_is_deleted, call_store
from .twiml import public_url

RECORDING_DOWNLOAD_ATTEMPTS = 6
RECORDING_RETRY_DELAY_SECONDS = 1.5


def recording_status(result: dict) -> str:
    if result.get("started"):
        return "recording"
    if result.get("disabled"):
        return "disabled"
    return "failed"


def recording_media_url(recording_sid: str) -> str | None:
    """Build, rather than trust, the credentialed recording download URL."""
    account = os.environ.get("TWILIO_ACCOUNT_SID", "")
    if not account or not re.fullmatch(r"RE[a-fA-F0-9]{32}", recording_sid):
        return None
    return (
        f"https://api.twilio.com/2010-04-01/Accounts/{account}"
        f"/Recordings/{recording_sid}"
    )


def audit_recording_payload() -> dict[str, str]:
    return {
        "RecordingChannels": "dual",
        "RecordingTrack": "both",
        "RecordingStatusCallback": public_url("/twilio/recording"),
        "RecordingStatusCallbackMethod": "POST",
        "RecordingStatusCallbackEvent": "completed absent",
    }


def _full_call_recording_enabled(to_number: str) -> bool:
    for number in call_store().list_provider_numbers(
        provider="twilio",
        managed_mode="amd",
    ):
        if number.phone_number == to_number:
            return number.configuration.get("record_full_calls") is True
    return os.environ.get("PSTN_RECORD_ALL_CALLS", "false").lower() in (
        "1",
        "true",
        "yes",
    )


async def start_audit_recording(call_sid: str, to_number: str = "") -> dict:
    """Start the full-call audit recording while handling the answer webhook."""
    if not _full_call_recording_enabled(to_number):
        return {"started": False, "disabled": True, "error": "Recording disabled by configuration"}
    account = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not account or not token:
        return {"started": False, "error": "Twilio credentials unavailable"}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account}/Calls/{call_sid}/Recordings.json"
    try:
        async with ClientSession(
            headers={"Authorization": encode_basic_auth(account, token)},
            timeout=ClientTimeout(total=2.5),
        ) as http:
            async with http.post(
                url,
                data=audit_recording_payload(),
            ) as resp:
                payload = await resp.json(content_type=None)
                return {
                    "started": resp.status in (200, 201),
                    "http": resp.status,
                    "sid": payload.get("sid"),
                    "status": payload.get("status"),
                }
    except Exception as exc:
        return {"started": False, "error": str(exc)[:160]}


async def download_recording(call: dict, url: str, recording_sid: str, kind: str) -> None:
    sid = call["call_sid"]
    if call_is_deleted(sid):
        return
    out_dir = call_results_dir(sid)
    authorization = encode_basic_auth(
        os.environ.get("TWILIO_ACCOUNT_SID", ""), os.environ.get("TWILIO_AUTH_TOKEN", "")
    )
    # Twilio recordings become fetchable shortly after the callback.
    async with ClientSession(headers={"Authorization": authorization}, timeout=ClientTimeout(total=15)) as http:
        for attempt in range(RECORDING_DOWNLOAD_ATTEMPTS):
            await asyncio.sleep(RECORDING_RETRY_DELAY_SECONDS * attempt)
            with contextlib.suppress(Exception):
                async with http.get(url + ".wav") as resp:
                    if resp.status == 200:
                        audio = await resp.read()
                        if call_is_deleted(sid):
                            return
                        prefix = "full-call" if kind == "full_call" else "message"
                        path = out_dir / f"{prefix}-{recording_sid}.wav"
                        out_dir.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(audio)
                        files = call.setdefault("recording_files", [])
                        if str(path) not in files:
                            files.append(str(path))
                        if kind == "message":
                            files = call.setdefault("analysis_recording_files", [])
                            if str(path) not in files:
                                files.append(str(path))
                        saved = call_store().upsert_recording(
                            recording_id=recording_sid,
                            call_id=sid,
                            provider="twilio",
                            kind=kind,
                            status="completed",
                            provider_url=url,
                            local_path=str(path),
                        )
                        if saved is None:
                            path.unlink(missing_ok=True)
                            with contextlib.suppress(OSError):
                                out_dir.rmdir()
                            return
                        print(f"[recording] {sid} → {path.name}")
                        return
    if call_is_deleted(sid):
        return
    call_store().upsert_recording(
        recording_id=recording_sid,
        call_id=sid,
        provider="twilio",
        kind=kind,
        status="download_failed",
        provider_url=url,
    )
    if kind == "full_call":
        call_store().upsert_incoming_call(
            call_id=sid,
            status=call.get("status", "completed"),
            recording_status="download_failed",
        )
    print(f"[recording] {sid} FAILED to download {url}")
