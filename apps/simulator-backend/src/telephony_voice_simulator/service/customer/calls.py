"""Queue persona snapshots and place outbound customer calls."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from ...storage.sqlite import SimulatorStore
from ...telephony.twilio import twiml
from ...telephony.twilio.management import TwilioManagementError, TwilioNumberManager
from ..errors import ConflictError, NotFoundError, ValidationError
from .agents import ensure_variant
from .catalog import _lines, profiles, resolve_persona


def queue_persona(
    store: SimulatorStore,
    number: str,
    *,
    persona_id: str | None = None,
    inline: Any = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Queue a persona for the next call on a persona line; a copy is queued, not a reference."""
    if number not in _lines():
        raise ValidationError(f"{number} is not a persona line in PSTN_CUSTOMER_PROFILES")
    persona = resolve_persona(persona_id, inline)
    ensure_variant(persona)
    return store.enqueue_customer_persona(number, persona, label)


def dial(
    store: SimulatorStore,
    number: str,
    to: str,
    *,
    persona_id: str | None = None,
    inline: Any = None,
    label: str | None = None,
    send_digits: str = "",
) -> dict[str, Any]:
    """Have a persona line call ``to`` (a dealership's inbound line), optionally queuing a persona.

    ``send_digits`` is keyed after the dealership answers and before the persona speaks,
    for an inbound line that opens with a DTMF menu.

    The call reaches the agent under test as an ordinary inbound call, so no agent-side
    trigger or credential is needed. ``to`` must be an allowed caller, because the customer
    webhook only connects personas to allowed numbers.
    """
    if number not in _lines():
        raise ValidationError(f"{number} is not a persona line in PSTN_CUSTOMER_PROFILES")
    if to not in profiles()["allowed_callers"]:
        raise ValidationError(f"{to} is not in PSTN_CUSTOMER_ALLOWED_CALLERS")
    if not os.environ.get("PUBLIC_BASE_URL"):
        raise ValidationError("Set PUBLIC_BASE_URL so Twilio can reach the customer webhook")
    if not re.fullmatch(r"[0-9*#wW]{0,32}", send_digits):
        raise ValidationError("send_digits takes up to 32 of 0-9, *, # and w (half-second pause)")
    queued = None
    if persona_id or inline is not None:
        # The call claims the oldest queued persona, so an earlier one would take its place.
        if waiting := store.list_customer_persona_queue(number):
            raise ConflictError(f"{len(waiting)} persona(s) already queued on {number}")
        queued = queue_persona(store, number, persona_id=persona_id, inline=inline, label=label)
    status_path = "/twilio/status" + (f"?persona_queue={queued['id']}" if queued else "")
    try:
        call = asyncio.run(TwilioNumberManager().place_call(
            from_number=number, to_number=to, url=twiml.public_url("/twilio/customer"),
            status_callback=twiml.public_url(status_path), send_digits=send_digits,
        ))
    except TwilioManagementError as exc:
        if queued:
            # Nothing will claim the persona, so it must not wait for the next real call.
            store.cancel_customer_persona(queued["id"])
        raise ConflictError(str(exc)) from exc
    return {"call_id": call.get("sid"), "from": number, "to": to, "queued": queued}


def list_queue(store: SimulatorStore, number: str | None = None) -> list[dict[str, Any]]:
    return store.list_customer_persona_queue(number)


def cancel_queued(store: SimulatorStore, queue_id: str) -> dict[str, Any]:
    if not store.cancel_customer_persona(queue_id):
        raise NotFoundError(f"Nothing queued with id {queue_id}")
    return {"cancelled": queue_id}
