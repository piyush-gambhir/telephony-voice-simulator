#!/usr/bin/env python3
"""Surgically attach or restore one Twilio number.

Unlike an account-wide provisioning script, every mutating operation requires
an explicit phone number. Attach writes a local backup before changing the
number, and restore consumes that backup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from urllib.parse import urljoin

import httpx

API = "https://api.twilio.com/2010-04-01"


def _client() -> tuple[httpx.Client, str]:
    account = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not account or not token:
        raise ValueError("set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
    return httpx.Client(auth=(account, token), timeout=30), account


def _number(client: httpx.Client, account: str, phone_number: str) -> dict:
    response = client.get(
        f"{API}/Accounts/{account}/IncomingPhoneNumbers.json",
        params={"PhoneNumber": phone_number},
    )
    response.raise_for_status()
    matches = response.json().get("incoming_phone_numbers", [])
    if len(matches) != 1:
        raise ValueError(f"expected one Twilio number matching {phone_number}, found {len(matches)}")
    return matches[0]


def _snapshot(record: dict) -> dict:
    keys = (
        "sid",
        "phone_number",
        "friendly_name",
        "voice_url",
        "voice_method",
        "status_callback",
        "status_callback_method",
    )
    return {
        "saved_at": dt.datetime.now(dt.UTC).isoformat(),
        **{key: record.get(key) for key in keys},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list account numbers and their voice URLs")

    attach = commands.add_parser("attach", help="point one number at a simulator route")
    attach.add_argument("--number", required=True)
    attach.add_argument(
        "--route",
        choices=("ivr", "amd"),
        default="ivr",
        help="unified backend route to attach",
    )
    attach.add_argument("--base-url", default=os.environ.get("PUBLIC_BASE_URL", ""))
    attach.add_argument("--voice-url", help="explicit webhook URL, including Serverless URLs")
    attach.add_argument("--status-callback-url")
    attach.add_argument("--backup", type=Path)
    attach.add_argument("--dry-run", action="store_true")

    restore = commands.add_parser("restore", help="restore one number from an attach backup")
    restore.add_argument("--number", required=True)
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        client, account = _client()
        if args.command == "list":
            response = client.get(
                f"{API}/Accounts/{account}/IncomingPhoneNumbers.json",
                params={"PageSize": 100},
            )
            response.raise_for_status()
            for record in response.json().get("incoming_phone_numbers", []):
                print(
                    f"{record['phone_number']}  "
                    f"voice_url={record.get('voice_url') or '(none)'}  "
                    f"name={record.get('friendly_name') or ''}"
                )
            return 0

        record = _number(client, account, args.number)
        if args.command == "attach":
            base = args.base_url.rstrip("/")
            route = "/twilio/ivr" if args.route == "ivr" else "/twilio/voice"
            voice_url = args.voice_url or (urljoin(f"{base}/", route.lstrip("/")) if base else "")
            if not voice_url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
                raise ValueError("provide an HTTPS --base-url or --voice-url")
            backup = args.backup or Path("results") / "twilio" / f"{record['sid']}.json"
            payload = {
                "VoiceUrl": voice_url,
                "VoiceMethod": "POST",
                "FriendlyName": "telephony-voice-simulator-line",
            }
            if args.status_callback_url:
                payload.update(
                    {
                        "StatusCallback": args.status_callback_url,
                        "StatusCallbackMethod": "POST",
                    }
                )
            print(json.dumps({"number": args.number, "change": payload}, indent=2))
            if args.dry_run:
                return 0
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps(_snapshot(record), indent=2) + "\n")
            response = client.post(
                f"{API}/Accounts/{account}/IncomingPhoneNumbers/{record['sid']}.json",
                data=payload,
            )
            response.raise_for_status()
            print(f"attached {args.number}; previous configuration saved to {backup}")
            return 0

        snapshot = json.loads(args.backup.read_text())
        if snapshot.get("sid") != record["sid"] or snapshot.get("phone_number") != args.number:
            raise ValueError("backup does not belong to the selected number")
        payload = {
            "VoiceUrl": snapshot.get("voice_url") or "",
            "VoiceMethod": snapshot.get("voice_method") or "POST",
            "StatusCallback": snapshot.get("status_callback") or "",
            "StatusCallbackMethod": snapshot.get("status_callback_method") or "POST",
            "FriendlyName": snapshot.get("friendly_name") or "restored-number",
        }
        print(json.dumps({"number": args.number, "restore": payload}, indent=2))
        if args.dry_run:
            return 0
        response = client.post(
            f"{API}/Accounts/{account}/IncomingPhoneNumbers/{record['sid']}.json",
            data=payload,
        )
        response.raise_for_status()
        print(f"restored {args.number} from {args.backup}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, httpx.HTTPError) as exc:
        print(f"Twilio provisioning failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
