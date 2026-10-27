"""ElevenLabs Agents: hand Twilio calls to an agent, keep its settings in sync, read transcripts.

The simulator keeps its own Twilio numbers. A call is registered with ElevenLabs, which
answers with TwiML that streams the call to the agent; nothing is imported into the
ElevenLabs dashboard.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

API_URL = "https://api.elevenlabs.io"
TIMEOUT_SECONDS = 10.0


class ElevenLabsError(RuntimeError):
    """The ElevenLabs API was unreachable or refused the request."""


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise ValueError("Set ELEVENLABS_API_KEY to use the synthetic customer")
    return key


def customer_agent_id() -> str:
    agent_id = os.environ.get("ELEVENLABS_CUSTOMER_AGENT_ID", "")
    if not agent_id:
        raise ValueError("Set ELEVENLABS_CUSTOMER_AGENT_ID to the persona agent's ID")
    return agent_id


def _headers() -> dict[str, str]:
    return {"xi-api-key": api_key()}


def _checked(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        detail = response.text[:300]
        raise ElevenLabsError(f"ElevenLabs returned {response.status_code}: {detail}")
    return response.json()


async def register_call(
    *,
    agent_id: str,
    from_number: str,
    to_number: str,
    direction: str,
    user_id: str,
    dynamic_variables: dict[str, str],
    client: httpx.AsyncClient | None = None,
) -> str:
    """TwiML that connects this Twilio call to the agent.

    ``user_id`` is stored on the conversation, so its transcript can be found later.
    """
    body = {
        "agent_id": agent_id,
        "from_number": from_number,
        "to_number": to_number,
        "direction": direction,
        "conversation_initiation_client_data": {
            "user_id": user_id,
            "dynamic_variables": dynamic_variables,
        },
    }
    owned = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        response = await client.post(
            f"{API_URL}/v1/convai/twilio/register-call", json=body, headers=_headers()
        )
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"ElevenLabs request failed: {exc}") from exc
    finally:
        if owned:
            await client.aclose()
    twiml = _checked(response)
    if not isinstance(twiml, str) or "<Response" not in twiml:
        raise ElevenLabsError("ElevenLabs did not return TwiML for the call")
    return twiml


def _request(method: str, path: str, client: httpx.Client | None = None, **kwargs: Any) -> Any:
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        return _checked(client.request(method, f"{API_URL}{path}", headers=_headers(), **kwargs))
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"ElevenLabs request failed: {exc}") from exc
    finally:
        if owned:
            client.close()


def update_agent(
    agent_id: str, conversation_config: dict[str, Any], *, client: httpx.Client | None = None
) -> dict[str, Any]:
    return _request(
        "PATCH",
        f"/v1/convai/agents/{agent_id}",
        client,
        json={"conversation_config": conversation_config},
    )


def find_conversation(
    agent_id: str, user_id: str, *, client: httpx.Client | None = None
) -> dict[str, Any] | None:
    """The agent's conversation registered with ``user_id``, if ElevenLabs has logged it."""
    listing = _request(
        "GET",
        "/v1/convai/conversations",
        client,
        params={"agent_id": agent_id, "user_id": user_id, "page_size": 1},
    )
    conversations = listing.get("conversations") or []
    return conversations[0] if conversations else None


def get_conversation(conversation_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    return _request("GET", f"/v1/convai/conversations/{conversation_id}", client)
