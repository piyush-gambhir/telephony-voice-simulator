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
AGENT_ENV = {
    "chained": "ELEVENLABS_CUSTOMER_AGENT_ID",
    "realtime": "ELEVENLABS_CUSTOMER_REALTIME_AGENT_ID",
}
# Accepted by ElevenLabs for simulations as of 2026-10; gpt-6-sol and gpt-5.5 are not.
DEFAULT_EVALUATION_MODEL = "gpt-6-luna"


class ElevenLabsError(RuntimeError):
    """The ElevenLabs API was unreachable or refused the request."""


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise ValueError("Set ELEVENLABS_API_KEY to use the synthetic customer")
    return key


def customer_agent_id(engine: str = "chained") -> str:
    """The persona agent for an engine: chained (ASR, LLM, TTS) or Realtime speech-to-speech."""
    key = AGENT_ENV[engine]
    agent_id = os.environ.get(key, "")
    if not agent_id:
        raise ValueError(f"Set {key} to the {engine} persona agent's ID")
    return agent_id


def evaluation_model() -> str:
    """The OpenAI model ElevenLabs uses to judge tests and play the simulated dealership."""
    return os.environ.get("ELEVENLABS_EVALUATION_MODEL", "") or DEFAULT_EVALUATION_MODEL


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
    config_override: dict[str, Any] | None = None,
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
    if config_override:
        body["conversation_initiation_client_data"]["conversation_config_override"] = (
            config_override
        )
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
    agent_id: str, body: dict[str, Any], *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """PATCH the agent; ``body`` holds ``conversation_config`` and/or ``platform_settings``."""
    return _request("PATCH", f"/v1/convai/agents/{agent_id}", client, json=body)


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


def find_test(name: str, *, client: httpx.Client | None = None) -> str | None:
    """The ID of the test with exactly this name, if one exists."""
    listing = _request(
        "GET", "/v1/convai/agent-testing", client, params={"search": name, "page_size": 100}
    )
    for test in listing.get("tests") or []:
        if test.get("name") == name:
            return test["id"]
    return None


def save_test(body: dict[str, Any], *, client: httpx.Client | None = None) -> str:
    """Create the test named ``body["name"]``, or replace the one that already has that name."""
    test_id = find_test(body["name"], client=client)
    if test_id:
        _request("PUT", f"/v1/convai/agent-testing/{test_id}", client, json=body)
        return test_id
    return _request("POST", "/v1/convai/agent-testing/create", client, json=body)["id"]


def run_tests(
    agent_id: str, test_ids: list[str], *, repeat: int = 1, client: httpx.Client | None = None
) -> str:
    """Start the tests on the agent and return the invocation ID."""
    result = _request(
        "POST",
        f"/v1/convai/agents/{agent_id}/run-tests",
        client,
        json={"tests": [{"test_id": t} for t in test_ids], "repeat_count": repeat},
    )
    return result["id"]


def get_invocation(invocation_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    return _request("GET", f"/v1/convai/test-invocations/{invocation_id}", client)
