"""Match inbound calls to managed lines, claim queued runs and record run progress."""

from __future__ import annotations

from aiohttp import web

from ...identifiers import normalize_e164
from . import state
from .state import call_store, iso
from .twiml import twiml


def _normalized_address(address: str) -> str:
    return normalize_e164(address) or address.strip()


def _managed_twilio_matches(address: str) -> list[tuple[object, object]]:
    normalized = _normalized_address(address)
    matches: list[tuple[object, object]] = []
    for endpoint in call_store().list_endpoints():
        if _normalized_address(endpoint.address) != normalized:
            continue
        connection = call_store().get_connection(endpoint.connection_id)
        if connection is not None and connection.provider == "twilio":
            matches.append((endpoint, connection))
    return matches


def endpoint_is_enabled(address: str) -> bool:
    """Fail closed for a control-plane-managed Twilio address.

    Addresses without endpoint rows answer with ``PSTN_DEFAULT_SCENARIO``. Once an
    address is represented by a Twilio endpoint, at least one matching endpoint
    and its provider connection must be enabled.
    """

    matches = _managed_twilio_matches(address)
    managed = any(
        _normalized_address(item) == _normalized_address(address)
        for item in call_store().list_managed_provider_addresses("twilio")
    )
    return not managed or any(
        endpoint.enabled and connection.enabled for endpoint, connection in matches
    )


def _enabled_twilio_endpoint(address: str):
    return next(
        (
            endpoint
            for endpoint, connection in _managed_twilio_matches(address)
            if endpoint.enabled and connection.enabled
        ),
        None,
    )


def _dequeue_pstn_assignment(address: str, call_id: str) -> dict[str, str | None] | None:
    """Claim a persisted console assignment for one call, matching any stored address format."""

    candidates = [address]
    candidates.extend(
        endpoint.address
        for endpoint, _connection in _managed_twilio_matches(address)
        if endpoint.address not in candidates
    )
    for candidate in candidates:
        assignment = call_store().dequeue_provider_run("twilio", candidate, call_id)
        if assignment is not None:
            return assignment
    return None


def pick_call_assignment(address: str, call_id: str) -> dict[str, str | None]:
    """Resolve managed routing first; unmanaged numbers use the global default."""

    endpoint = _enabled_twilio_endpoint(address)
    if endpoint is not None:
        queued = _dequeue_pstn_assignment(address, call_id)
        if endpoint.routing_mode == "fixed":
            return {
                "scenario": endpoint.default_scenario
                or (queued.get("scenario") if queued is not None else None),
                "run_id": queued.get("run_id") if queued is not None else None,
                "queue_claim": queued,
            }
        if queued is not None:
            return {**queued, "queue_claim": queued}
        return {"scenario": endpoint.default_scenario, "run_id": None}
    return {"scenario": state.STATE.default_scenario or None, "run_id": None}


def update_simulation_run(
    run_id: str | None,
    *,
    status: str,
    call_id: str,
    result: dict[str, object] | None = None,
    completed: bool = False,
) -> None:
    if not run_id:
        return
    run = call_store().get_run(run_id)
    if run is None or run.completed_at is not None:
        return
    merged_result = {**run.result, "mode": "pstn", "call_id": call_id, **(result or {})}
    timeline = [
        *run.timeline,
        {
            "kind": "call",
            "label": "PSTN call completed" if completed else "PSTN call answered",
            "state": status,
            "event": "pstn_call_completed" if completed else "pstn_call_answered",
            "call_id": call_id,
            "at": iso(),
        },
    ]
    call_store().update_run(
        run,
        status=status,
        timeline=timeline,
        result=merged_result,
        completed=completed,
    )


def finish_simulation_run(call: dict, analysis: dict | None = None) -> None:
    run_id = str(call.get("run_id") or "") or None
    if run_id is None:
        return
    carrier_status = str(call.get("status") or "completed")
    result: dict[str, object] = {
        "graded": analysis.get("graded", True) if analysis is not None else False,
        "carrier_status": carrier_status,
        "duration_seconds": call.get("duration_s"),
    }
    if analysis is not None:
        result.update(
            {
                "passed": analysis.get("passed"),
                "analysis": analysis,
                "summary": (
                    "PSTN call completed and passed AMD checks."
                    if analysis.get("passed") is True
                    else "PSTN call completed; AMD evidence is incomplete."
                    if analysis.get("passed") is None
                    else "PSTN call completed; one or more AMD checks failed."
                ),
            }
        )
    else:
        result["summary"] = (
            "PSTN call reached a terminal carrier status after the runtime restarted; "
            "recording analysis was unavailable."
        )
    # A late recording may replace an indeterminate grade without replaying the
    # call-completed event or changing its original completion timestamp.
    run = call_store().get_run(run_id)
    if (run is not None and run.completed_at is not None and analysis is not None
            and run.result.get("call_id") == call["call_sid"]
            and run.result.get("mode") == "pstn"):
        call_store().update_run(run, status=run.status, result={**run.result, **result})
        return
    update_simulation_run(
        run_id,
        status="completed" if carrier_status == "completed" else "failed",
        call_id=str(call["call_sid"]),
        result=result,
        completed=True,
    )


def reject_unavailable_endpoint(
    *,
    call_sid: str,
    from_address: str,
    to_address: str,
) -> web.Response | None:
    if endpoint_is_enabled(to_address):
        return None
    candidates = [to_address]
    candidates.extend(
        endpoint.address
        for endpoint, _connection in _managed_twilio_matches(to_address)
        if endpoint.address not in candidates
    )
    for address in candidates:
        call_store().fail_provider_queue(
            "twilio",
            address,
            "Managed endpoint or provider was unavailable when the PSTN call arrived",
        )
    call_store().upsert_incoming_call(
        call_id=call_sid,
        from_address=from_address,
        to_address=to_address,
        status="rejected",
        recording_status="not_started",
        analysis={"rejection_reason": "endpoint_or_provider_disabled"},
    )
    return twiml("<Reject/>")
