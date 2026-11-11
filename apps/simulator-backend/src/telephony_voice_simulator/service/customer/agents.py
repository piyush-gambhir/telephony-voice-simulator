"""Provision ElevenLabs persona agents and resolve their pinned routing versions."""

from __future__ import annotations

import fcntl
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ... import personas
from ...integrations import elevenlabs
from ...paths import DATA_DIR
from ...telephony.twilio import customer
from ..errors import ConflictError
from .catalog import _catalog
from .common import _translated

ROUTING_TTL_SECONDS = 300.0
# Dashboard changes can invalidate agent IDs, so routing entries expire.
_VARIANT_AGENTS: dict[str, tuple[float, str]] = {}
_PINNED_BRANCHES: dict[str, tuple[float, str]] = {}
# The thread lock and file lock serialize provisioning within and across processes.
_PROVISION_LOCK = threading.Lock()


def _cached(cache: dict[str, tuple[float, str]], key: str, lookup: Callable[[], str | None]
            ) -> str | None:
    hit = cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    value = lookup()
    if value is None:
        cache.pop(key, None)
    else:
        cache[key] = (time.monotonic() + ROUTING_TTL_SECONDS, value)
    return value


def forget_routing() -> None:
    """Drop cached agent and branch IDs, e.g. after ElevenLabs refused a registration."""
    _VARIANT_AGENTS.clear()
    _PINNED_BRANCHES.clear()


@contextmanager
def _provisioning() -> Iterator[None]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _PROVISION_LOCK, open(DATA_DIR / "elevenlabs-provisioning.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _pinned_branch(agent_id: str) -> str | None:
    return _cached(_PINNED_BRANCHES, agent_id, lambda: _translated(
        lambda: elevenlabs.find_branch(agent_id, customer.PINNED_BRANCH)
    ))


def _variant_agent(name: str) -> str | None:
    return _cached(_VARIANT_AGENTS, name, lambda: _translated(lambda: elevenlabs.find_agent(name)))


def resolve_agent(persona: dict[str, Any]) -> tuple[str, str | None]:
    """The persona's agent (its engine's base agent, or its variant) and pinned branch.

    A base agent's branch is None until ``customer sync-agent`` has created it; its calls
    then run on the live configuration. A variant must be fully provisioned, branch
    included, because an unsynced copy would silently lack its noise and turn settings.
    """
    base = _translated(lambda: elevenlabs.customer_agent_id(persona["engine"]))
    settings = personas.variant(persona)
    if settings is None:
        return base, _pinned_branch(base)
    name = personas.variant_name(settings)
    agent_id = _variant_agent(name)
    branch = _pinned_branch(agent_id) if agent_id else None
    if agent_id is None or branch is None:
        raise ConflictError(f"ElevenLabs agent '{name}' is not provisioned; run customer sync-agent")
    return agent_id, branch


def _configured_engines() -> list[str]:
    return [e for e, key in elevenlabs.AGENT_ENV.items() if os.environ.get(key)]


def _sync_one(agent_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Write the settings to the agent's main branch, then move the pinned branch to them.

    Turns on ElevenLabs versioning for the agent, which cannot be turned off.
    """
    main = _translated(
        lambda: elevenlabs.update_agent(agent_id, settings, enable_versioning=True)
    )
    branch = _translated(lambda: elevenlabs.find_branch(agent_id, customer.PINNED_BRANCH))
    if branch is None:
        _translated(lambda: elevenlabs.create_branch(
            agent_id, customer.PINNED_BRANCH, main["version_id"],
            "Calls and tests from telephony-voice-simulator. Moved only by customer sync-agent.",
        ))
        branch = _translated(lambda: elevenlabs.find_branch(agent_id, customer.PINNED_BRANCH))
        if branch is None:
            raise ConflictError(f"ElevenLabs did not create the pinned branch on {agent_id}")
    else:
        _translated(lambda: elevenlabs.rebase_branch(agent_id, branch))
    version = _translated(lambda: elevenlabs.branch_version(agent_id, branch))
    _PINNED_BRANCHES[agent_id] = (time.monotonic() + ROUTING_TTL_SECONDS, branch)
    return {"agent_id": agent_id, "branch_id": branch, "version_id": version}


def _sync_variant(variant: dict[str, Any]) -> dict[str, Any]:
    """Create the variant agent from its engine's base agent if needed, then sync it.

    The base agent's current voice is copied each time, so a voice picked in the
    dashboard reaches every variant at the next sync.
    """
    base = _translated(lambda: elevenlabs.customer_agent_id(variant["engine"]))
    name = personas.variant_name(variant)
    agent_id = _translated(lambda: elevenlabs.find_agent(name))
    if agent_id is None:
        agent_id = _translated(lambda: elevenlabs.duplicate_agent(base, name))
    settings = customer.agent_settings(variant["engine"], variant)
    voice = (_translated(lambda: elevenlabs.get_agent(base))["conversation_config"]
             .get("tts", {}).get("voice_id"))
    if voice:
        settings["conversation_config"]["tts"]["voice_id"] = voice
    synced = _sync_one(agent_id, settings)
    # Cached only once configured and pinned, so calls never reach a half-made copy.
    _VARIANT_AGENTS[name] = (time.monotonic() + ROUTING_TTL_SECONDS, agent_id)
    return {"name": name, **synced}


def sync_agent() -> dict[str, Any]:
    """Configure every persona agent and move each one's pinned branch to the result.

    Base agents get the prompt, audio format, call limit, model and override permissions;
    each variant needed by a catalog persona on a configured engine is created if missing
    and gets the same plus its background sound and turn-taking.
    """
    with _provisioning():
        return _sync_all()


def _sync_all() -> dict[str, Any]:
    engines = _configured_engines()
    if "chained" not in engines:
        _translated(elevenlabs.customer_agent_id)
    bases = {
        engine: _sync_one(elevenlabs.customer_agent_id(engine), customer.agent_settings(engine))
        for engine in engines
    }
    variants: dict[str, dict[str, Any]] = {}
    for persona in _catalog().values():
        settings = personas.variant(persona)
        if settings and settings["engine"] in engines:
            name = personas.variant_name(settings)
            if name not in variants:
                variants[name] = {**_sync_variant(settings), "personas": []}
            variants[name]["personas"].append(persona["id"])
    return {"agents": bases, "variants": list(variants.values())}


def ensure_variant(persona: dict[str, Any]) -> None:
    """Provision a missing persona variant without moving an existing pinned branch."""
    if settings := personas.variant(persona):
        with _provisioning():
            name = personas.variant_name(settings)
            agent_id = _variant_agent(name)
            if agent_id is None or _pinned_branch(agent_id) is None:
                _sync_variant(settings)
