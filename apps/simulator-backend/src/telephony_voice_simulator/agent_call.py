"""Dispatch a LiveKit agent to call the simulator's phone number (LiveKit adapter).

This is the optional LiveKit-specific path. It creates a LiveKit agent
dispatch addressed to your deployed worker with an outbound runtime payload;
the agent then dials the simulator's number through its SIP trunk — real
carrier, real audio, real DTMF relay — and the hosted machine answers with
whichever scenario is queued (or the number's default).

Any non-LiveKit agent (Vapi, Retell, Bland, Twilio, your own) doesn't need
this at all: just point that agent at the simulator's phone number and grade
with ``grade_hosted.py``. The number is the only integration surface.

Environment:
  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET   your LiveKit project
  AGENT_NAME                the deployed worker's agent_name to dispatch
  SIM_NUMBER                the simulator's phone number to dial
  SIM_CALLER_ID             an account-owned caller ID for the SIP trunk
  AGENT_WEBHOOK_URL         (optional) where the agent posts call lifecycle
  METADATA_TEMPLATE         (optional) dispatch metadata JSON template
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN   to queue the scenario + grade

Usage:
    python -m telephony_voice_simulator.agent_call --scenario stock_voicemail_beep_1000
    python -m telephony_voice_simulator.agent_call --scenario dtmf_gate_honored --no-queue
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

from .dispatch import build_metadata, create_dispatch
from .paths import TEMPLATES_DIR

SYNC = "https://sync.twilio.com/v1/Services/default"


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Set {name} (see the module docstring for the required env).")
    return v


def _twilio_auth() -> tuple[str, str]:
    return (_env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN"))


def queue_scenario(scenario: str, sim_number: str) -> None:
    name = f"queue-{sim_number}"
    auth = _twilio_auth()
    # SET (not append): a stale entry from a failed dial otherwise shifts
    # every later run onto the wrong scenario.
    payload = {"Data": json.dumps({"queue": [scenario]}), "Ttl": "3600"}
    resp = httpx.post(f"{SYNC}/Documents/{name}", auth=auth, data=payload)
    if resp.status_code == 404:
        httpx.post(f"{SYNC}/Documents", auth=auth,
                   data={"UniqueName": name, **payload}).raise_for_status()
    else:
        resp.raise_for_status()
    print(f"queue set to [{scenario}] on {sim_number}")


async def trigger(scenario: str, queue: bool) -> tuple[str, str]:
    sim_number = _env("SIM_NUMBER")
    agent_name = _env("AGENT_NAME")
    if queue:
        queue_scenario(scenario, sim_number)
    call_id = f"sim-{uuid.uuid4().hex[:12]}"
    room = f"voicesim-{call_id[-6:]}"
    template = Path(
        os.environ.get("METADATA_TEMPLATE", str(TEMPLATES_DIR / "outbound.metadata.json"))
    )
    metadata = build_metadata(
        template,
        call_id=call_id,
        room_name=room,
        webhook_url=os.environ.get("AGENT_WEBHOOK_URL", ""),
        config_overrides=None,
    )
    metadata["phone_number"] = sim_number
    # Most SIP trunks require an account-owned caller ID; without it the INVITE
    # is typically rejected and the call dies before it connects.
    caller_id = os.environ.get("SIM_CALLER_ID")
    if caller_id:
        metadata["agent_phone"] = caller_id
    await create_dispatch(
        url=_env("LIVEKIT_URL"),
        api_key=_env("LIVEKIT_API_KEY"),
        api_secret=_env("LIVEKIT_API_SECRET"),
        agent_name=agent_name,
        room_name=room,
        metadata=metadata,
    )
    print(f"dispatched agent={agent_name} room={room} call_id={call_id} → dials {sim_number}")
    return call_id, sim_number


def wait_and_grade(sim_number: str, dispatched_at: float, timeout: float = 240.0) -> int:
    """Wait for a NEW completed inbound leg at the sim number, then grade it."""
    import email.utils

    from .pstn.grade_hosted import _grade_call

    auth = _twilio_auth()
    account = auth[0]
    api = "https://api.twilio.com/2010-04-01"
    print("waiting for the inbound leg at the sim number", end="", flush=True)
    deadline = time.time() + timeout
    seen: str | None = None
    while time.time() < deadline:
        time.sleep(10)
        print(".", end="", flush=True)
        resp = httpx.get(
            f"{api}/Accounts/{account}/Calls.json",
            params={"To": sim_number, "PageSize": 5},
            auth=auth,
        )
        fresh = []
        for c in resp.json().get("calls", []):
            if not c["direction"].startswith("inbound"):
                continue
            created = email.utils.parsedate_to_datetime(c["date_created"]).timestamp()
            if created >= dispatched_at - 30:
                fresh.append(c)
        if fresh and fresh[0]["status"] in ("completed", "no-answer", "busy", "failed"):
            seen = fresh[0]["sid"]
            break
    print()
    if seen is None:
        print("no completed inbound leg observed at the sim number")
        return 1
    print("waiting 20s for recording availability")
    time.sleep(20)
    client = httpx.Client(auth=auth, timeout=60)
    return _grade_call(client, account, seen)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="stock_voicemail_beep_1000")
    parser.add_argument("--no-queue", action="store_true",
                        help="don't queue; the number's static/default answers")
    parser.add_argument("--no-wait", action="store_true", help="dispatch only")
    args = parser.parse_args()

    dispatched_at = time.time()
    _call_id, sim_number = asyncio.run(trigger(args.scenario, queue=not args.no_queue))
    if args.no_wait:
        return 0
    return wait_and_grade(sim_number, dispatched_at)


if __name__ == "__main__":
    sys.exit(main())
