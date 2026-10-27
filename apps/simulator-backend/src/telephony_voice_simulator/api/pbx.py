"""Dealer-group PBX model: map, call routing, named cases and capacity. Places no calls."""

from __future__ import annotations

from aiohttp import web

from ..service import ValidationError, pbx
from .common import (
    json_object,
    optional_int_field,
    optional_string_field,
    string_field,
    string_list_field,
    string_map_field,
)


async def dealer_map(_request: web.Request) -> web.Response:
    return web.json_response(pbx.dealer_map())


async def route_call(request: web.Request) -> web.Response:
    body = await json_object(request)
    occupied = body.get("occupied_channels", {})
    if not isinstance(occupied, dict) or not all(
        isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        for key, value in occupied.items()
    ):
        raise ValidationError("occupied_channels must map trunk ids to whole numbers")
    # Zero patience is valid (the caller hangs up as soon as they would wait).
    patience = optional_int_field(body, "patience_seconds")
    return web.json_response(
        pbx.route_call(
            to_number=string_field(body, "to_number"),
            from_number=string_field(body, "from_number", "+14085550131"),
            day=string_field(body, "day", "tue"),
            time=string_field(body, "time", "10:30"),
            date=optional_string_field(body, "date"),
            digits=string_list_field(body, "digits"),
            intent=string_field(body, "intent"),
            patience_seconds=600 if patience is None else patience,
            presence=string_map_field(body, "presence"),
            occupied_channels=occupied,
            glare_trunks=string_list_field(body, "glare_trunks"),
            transfer_method=optional_string_field(body, "transfer_method"),
            transfer_target=optional_string_field(body, "transfer_target"),
            transfer_agent=string_field(body, "transfer_agent", "bdc-overflow"),
        )
    )


async def list_cases(_request: web.Request) -> web.Response:
    return web.json_response(pbx.list_cases())


async def run_case(request: web.Request) -> web.Response:
    return web.json_response(pbx.run_named_case(request.match_info["case_id"]))


async def run_suite(_request: web.Request) -> web.Response:
    return web.json_response(pbx.run_all_cases())


async def capacity(request: web.Request) -> web.Response:
    raw_channels = request.query.get("channels")
    try:
        channels = int(raw_channels) if raw_channels else None
    except ValueError as exc:
        raise ValidationError("channels must be a whole number") from exc
    return web.json_response(pbx.site_capacity(request.query.get("site") or None, channels))


def register(app: web.Application) -> None:
    app.router.add_get("/api/pbx/map", dealer_map)
    app.router.add_post("/api/pbx/calls", route_call)
    app.router.add_get("/api/pbx/cases", list_cases)
    app.router.add_post("/api/pbx/cases/{case_id}/run", run_case)
    app.router.add_post("/api/pbx/suite", run_suite)
    app.router.add_get("/api/pbx/capacity", capacity)
