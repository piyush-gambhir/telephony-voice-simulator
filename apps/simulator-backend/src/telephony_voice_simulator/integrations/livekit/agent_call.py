"""Dispatch a LiveKit agent to call the simulator's phone number (LiveKit adapter).

This is the optional LiveKit-specific path. It creates a LiveKit agent
dispatch addressed to your deployed worker with an outbound runtime payload;
the agent then dials the simulator's number through its SIP trunk (real
carrier, real audio, real DTMF relay) and the hosted machine answers with
whichever scenario is queued (or the number's default).

Any non-LiveKit agent (Vapi, Retell, Bland, Twilio, your own) doesn't need
this at all: just point that agent at the simulator's phone number and grade
with ``telephony-voice-sim hosted grade``. The number is the only integration surface.

Environment:
  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET   your LiveKit project
  AGENT_NAME                the deployed worker's agent_name to dispatch
  SIM_NUMBER                the simulator's phone number to dial
  SIM_CALLER_ID             an account-owned caller ID for the SIP trunk
  AGENT_WEBHOOK_URL         (optional) where the agent posts call lifecycle
  METADATA_TEMPLATE         (optional) dispatch metadata JSON template
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN   to queue the scenario + grade

Used through ``telephony-voice-sim hosted agent-call``. Missing environment raises
``ValueError``; progress goes to the ``log`` callback.
"""

from __future__ import annotations

import email.utils
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from ...paths import TEMPLATES_DIR
from ...telephony.twilio.hosted.grade import inbound_legs, queue_scenario, twilio_client
from .dispatch import build_metadata, create_dispatch

Log = Callable[[str], None]


def _no_log(_line: str) -> None:
    return None


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise ValueError(f"Set {name} (see the agent_call module docstring for the required env).")
    return v


async def trigger(scenario: str, queue: bool, log: Log = _no_log) -> dict:
    """Queue the scenario (optionally) and dispatch the agent; returns the dispatch facts."""
    sim_number = _env("SIM_NUMBER")
    agent_name = _env("AGENT_NAME")
    if queue:
        client, _account = twilio_client()
        with client:
            pending = queue_scenario(client, sim_number, scenario)
        log(f"queued {scenario} on {sim_number} (queue={pending})")
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
    log(f"dispatched agent={agent_name} room={room} call_id={call_id}, dials {sim_number}")
    return {
        "agent_name": agent_name,
        "room": room,
        "call_id": call_id,
        "sim_number": sim_number,
        "scenario": scenario,
        "queued": queue,
    }


def wait_for_inbound_leg(
    sim_number: str, dispatched_at: float, timeout: float = 240.0, log: Log = _no_log
) -> str | None:
    """Wait for a NEW completed inbound leg at the sim number; returns its call SID."""
    client, account = twilio_client()
    log("waiting for the inbound leg at the sim number")
    deadline = time.time() + timeout
    with client:
        while time.time() < deadline:
            time.sleep(10)
            fresh = [
                c
                for c in inbound_legs(client, account, sim_number, 5)
                if email.utils.parsedate_to_datetime(c["date_created"]).timestamp()
                >= dispatched_at - 30
            ]
            if fresh and fresh[0]["status"] in ("completed", "no-answer", "busy", "failed"):
                return fresh[0]["sid"]
    return None

