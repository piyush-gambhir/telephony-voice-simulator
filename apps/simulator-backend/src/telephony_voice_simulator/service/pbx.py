"""Dealer-group PBX model: dial plan, routing, transfers, named cases and capacity.

Stateless: nothing here places a call or touches the database.
"""

from __future__ import annotations

from typing import Any

from ..domains.pbx.dealer import (
    CASES,
    CallRequest,
    TransferAttempt,
    attempt_transfer,
    capacity_report,
    compare_sites,
    default_group,
    get_case,
    route,
    run_case,
    run_suite,
    transfer_matrix,
)
from .errors import NotFoundError, ValidationError

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
PRESENCE_STATES = ("available", "busy", "with_customer", "offline", "dnd")
TRANSFER_METHODS = ("blind_refer", "attended_refer", "bridge", "dtmf_redial", "sip_302")


def dealer_map() -> dict[str, Any]:
    """Sites, trunks, DIDs, agents and the transfer matrix of the modelled group."""
    group = default_group()
    return {
        "group": group.label,
        "intersite_prefix": group.intersite_prefix,
        "sites": [
            {
                "id": site.id,
                "label": site.label,
                "platform": site.platform,
                "border_element": site.border_element,
                "main_did": site.main_did,
                "extension_range": f"{site.numbering_prefix}xxx",
                "attendant": site.attendant,
                "trunks": list(site.trunks),
                "night_target": site.night_target,
                "notes": site.notes,
            }
            for site in group.sites
        ],
        "trunks": [
            {
                "id": trunk.id,
                "label": trunk.label,
                "site": trunk.site,
                "peer_site": trunk.peer_site,
                "kind": trunk.kind,
                "signaling": trunk.signaling,
                "channels": trunk.channels,
                "dtmf_mode": trunk.dtmf_mode,
                "glare_prone": trunk.glare_prone,
            }
            for trunk in group.trunks
        ],
        "dids": [
            {
                "number": did.number,
                "label": did.label,
                "site": did.site,
                "target": did.target,
                "tracking_source": did.tracking_source,
            }
            for did in group.dids
        ],
        "agents": [
            {
                "id": agent.id,
                "label": agent.label,
                "site": agent.site,
                "ingress": agent.ingress,
                "address": agent.address,
                "reached_via": agent.reached_via,
                "receives_diversion": agent.receives_diversion,
                "notes": agent.notes,
            }
            for agent in group.agents
        ],
        "transfer_matrix": transfer_matrix(group),
    }


def route_call(
    *,
    to_number: str,
    from_number: str = "+14085550131",
    day: str = "tue",
    time: str = "10:30",
    date: str | None = None,
    digits: list[str] | tuple[str, ...] = (),
    intent: str = "",
    patience_seconds: int = 600,
    presence: dict[str, str] | None = None,
    occupied_channels: dict[str, int] | None = None,
    glare_trunks: list[str] | tuple[str, ...] = (),
    transfer_method: str | None = None,
    transfer_target: str | None = None,
    transfer_agent: str = "bdc-overflow",
) -> dict[str, Any]:
    """Route one call through the group and optionally attempt an agent transfer."""
    if day not in WEEKDAYS:
        raise ValidationError(f"Day must be one of {', '.join(WEEKDAYS)}")
    for extension, state in (presence or {}).items():
        if state not in PRESENCE_STATES:
            raise ValidationError(
                f"Presence for {extension} must be one of {', '.join(PRESENCE_STATES)}"
            )
    if transfer_method is not None and transfer_method not in TRANSFER_METHODS:
        raise ValidationError(f"Transfer method must be one of {', '.join(TRANSFER_METHODS)}")
    if transfer_method and not transfer_target:
        raise ValidationError("A transfer needs a target, for example ext:1200")
    group = default_group()
    request = CallRequest(
        to_number=to_number,
        from_number=from_number,
        day=day,  # type: ignore[arg-type]
        time=time,
        date=date,
        digits=tuple(digits),
        intent=intent,
        patience_seconds=patience_seconds,
        presence=dict(presence or {}),  # type: ignore[arg-type]
        occupied_channels=dict(occupied_channels or {}),
        glare_trunks=tuple(glare_trunks),
    )
    try:
        payload: dict[str, Any] = {"call": route(group, request).as_dict()}
        if transfer_method:
            payload["transfer"] = attempt_transfer(
                group,
                TransferAttempt(
                    agent=transfer_agent,
                    method=transfer_method,  # type: ignore[arg-type]
                    target=str(transfer_target),
                    call=request,
                ),
            ).as_dict()
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return payload


def list_cases() -> list[dict[str, str]]:
    return [{"id": case.id, "title": case.title, "teaches": case.teaches} for case in CASES]


def run_named_case(case_id: str) -> dict[str, Any]:
    case = get_case(case_id)
    if case is None:
        raise NotFoundError(f"Unknown dealer case '{case_id}'")
    return run_case(case)


def run_all_cases() -> dict[str, Any]:
    results = run_suite()
    return {
        "total": len(results),
        "passed": sum(1 for item in results if item["passed"]),
        "cases": [
            {
                "id": item["id"],
                "title": item["title"],
                "passed": item["passed"],
                "outcome": item["actual_outcome"],
                "transfer": item["actual_transfer"],
            }
            for item in results
        ],
    }


def site_capacity(site_id: str | None = None, channels: int | None = None) -> Any:
    """One site's capacity per integration pattern, or every site when none is given."""
    if channels is not None and channels < 1:
        raise ValidationError("Channels must be at least 1")
    group = default_group()
    if site_id is None:
        return compare_sites(group, channels=channels)
    if site_id not in {site.id for site in group.sites}:
        raise NotFoundError(f"Unknown site '{site_id}'")
    return capacity_report(group, site_id, channels=channels)
