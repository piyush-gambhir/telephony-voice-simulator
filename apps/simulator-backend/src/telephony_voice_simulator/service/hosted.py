"""The Twilio-hosted callee: deploy it, queue scenarios, smoke-test it, grade its calls.

Needs only Twilio credentials in the environment; no simulator server is involved.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from ..integrations.livekit import agent_call as livekit_agent_call
from ..telephony.twilio.hosted import deploy as hosted_deploy
from ..telephony.twilio.hosted import grade as hosted_grade
from .errors import ConflictError, NotFoundError, ValidationError

DEFAULT_SCENARIO = "stock_voicemail_beep_1000"


@contextmanager
def _translated() -> Iterator[None]:
    """Map the implementation modules' errors onto the service errors."""
    try:
        yield
    except (ValidationError, NotFoundError, ConflictError):
        raise
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except LookupError as exc:
        raise NotFoundError(str(exc.args[0] if exc.args else exc)) from exc
    except (httpx.HTTPError, RuntimeError) as exc:
        raise ConflictError(f"Twilio request failed: {exc}") from exc


def _passed(calls: list[dict[str, Any]]) -> bool:
    return bool(calls) and all(c["analysis"]["passed"] is True for c in calls)


def deploy(
    *,
    static_map: dict[str, str] | None = None,
    default_scenario: str = DEFAULT_SCENARIO,
    acknowledge_public_audio: bool = False,
    audio_deployment_allowlist: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Build and deploy the callee to Twilio Serverless.

    Refuses unless public audio is acknowledged and a reviewed allowlist is given.
    Returns the environment domain and the machine URL.
    """
    with _translated():
        domain = hosted_deploy.deploy(
            static_map or {},
            default_scenario,
            acknowledge_public_audio=acknowledge_public_audio,
            audio_deployment_allowlist=audio_deployment_allowlist,
            log=log or hosted_deploy._no_log,
        )
    return {"domain": domain, "machine_url": f"https://{domain}/machine"}


def queue_scenario(*, number: str, scenario: str) -> dict[str, Any]:
    """Append a scenario to the number's hosted queue, keeping existing claims."""
    with _translated():
        client, _account = hosted_grade.twilio_client()
        with client:
            queue = hosted_grade.queue_scenario(client, number, scenario)
    return {"number": number, "scenario": scenario, "queue": queue}


def smoke_call(
    *,
    number: str,
    scenario: str,
    from_number: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Queue a scenario, place a real call to the sim line, grade the result.

    Dials from ``from_number`` (or TWILIO_FROM_NUMBER). Returns the graded call.
    """
    with _translated():
        client, account = hosted_grade.twilio_client()
        with client:
            call = hosted_grade.smoke_call(
                client,
                account,
                number=number,
                scenario=scenario,
                from_number=from_number or os.environ.get("TWILIO_FROM_NUMBER", ""),
                log=log or hosted_grade._no_log,
            )
    if call is None:
        raise NotFoundError("the inbound leg has no simulator state (was it a sim call?)")
    return {"passed": _passed([call]), "calls": [call]}


def grade_calls(
    *,
    call_sid: str | None = None,
    number: str | None = None,
    last: int = 3,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Grade one call by SID, or the last ``last`` inbound legs at a number.

    Returns ``passed`` (True only when every graded call passed), the graded calls,
    and the SIDs skipped because they carry no simulator state.
    """
    if not call_sid and not number:
        raise ValidationError("pass a call SID or a number")
    log = log or hosted_grade._no_log
    calls: list[dict[str, Any]] = []
    skipped: list[str] = []
    with _translated():
        client, account = hosted_grade.twilio_client()
        with client:
            if call_sid:
                sids = [call_sid]
            else:
                sids = [c["sid"] for c in hosted_grade.inbound_legs(client, account, number, last)]
            for sid in sids:
                call = hosted_grade.grade_call_detail(client, account, sid, log=log)
                if call is None:
                    skipped.append(sid)
                else:
                    calls.append(call)
    if call_sid and not calls:
        raise NotFoundError(f"{call_sid}: no sim state doc (was this a sim call?)")
    return {"passed": _passed(calls), "calls": calls, "skipped": skipped}


async def agent_call(
    *,
    scenario: str = DEFAULT_SCENARIO,
    queue: bool = True,
    wait: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Long-running. Dispatch a deployed LiveKit agent to call the sim number.

    Optionally queues the scenario first (appending, so claims survive) and, with
    ``wait``, waits for the inbound leg and grades it. Returns the dispatch facts and,
    when waiting, ``passed`` and the graded call (``call`` is None if no leg was seen).
    """
    log = log or hosted_grade._no_log
    dispatched_at = time.time()
    with _translated():
        dispatch = await livekit_agent_call.trigger(scenario, queue, log)
    result: dict[str, Any] = {**dispatch, "passed": None, "call": None}
    if not wait:
        return result
    with _translated():
        sid = await asyncio.to_thread(
            livekit_agent_call.wait_for_inbound_leg, dispatch["sim_number"], dispatched_at, 240.0, log
        )
        if sid is None:
            log("no completed inbound leg observed at the sim number")
            result["passed"] = False
            return result
        log("waiting 20s for recording availability")
        await asyncio.sleep(20)
        graded = await asyncio.to_thread(grade_calls, call_sid=sid, log=log)
    result["call"] = graded["calls"][0]
    result["passed"] = graded["passed"]
    return result
