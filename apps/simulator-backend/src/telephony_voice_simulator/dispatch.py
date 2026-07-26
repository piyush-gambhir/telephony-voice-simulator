"""Create a LiveKit agent dispatch that starts a simulated outbound call.

The metadata shape is deliberately supplied by a JSON template so this
adapter can target different voice-agent implementations without coupling the
core scenario engine to one application's payload.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from livekit import api


def _deep_merge(base: dict, overrides: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def build_metadata(
    template_path: Path,
    *,
    call_id: str,
    room_name: str,
    webhook_url: str,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the metadata template and stamp the per-call fields.

    The template keeps whatever key layout the backend produces; we only
    override well-known fields plus any scenario-specific ``config_overrides``
    (e.g. ``{"voicemail_detection": {"beep_detection": true}}``), deep-merged.
    The callee identity is NOT in the payload — the agent derives it from
    ``phone_number`` (``phone-<number>``), and the runner mirrors that.
    """
    metadata = json.loads(template_path.read_text())
    metadata.pop("_comment", None)
    metadata["call_id"] = call_id
    metadata["room_name"] = room_name
    metadata["webhook_url"] = webhook_url
    metadata.setdefault("call_type", "outbound")
    metadata.setdefault("phone_number", "+15550100001")
    if config_overrides:
        metadata = _deep_merge(metadata, config_overrides)
    return metadata


async def create_dispatch(
    *,
    url: str,
    api_key: str,
    api_secret: str,
    agent_name: str,
    room_name: str,
    metadata: dict[str, Any],
) -> None:
    lkapi = api.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
    try:
        await lkapi.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=agent_name,
                room=room_name,
                metadata=json.dumps(metadata),
            )
        )
    finally:
        await lkapi.aclose()


def new_call_id(scenario_name: str) -> tuple[str, str]:
    """(call_id, room_name) pair for one simulated call."""
    call_id = f"sim-{uuid.uuid4().hex[:12]}"
    room = f"voicesim-{scenario_name[:24]}-{call_id[-6:]}"
    return call_id, room
