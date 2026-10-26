"""Drive an external voice agent through its REST API or in a LiveKit room."""

from __future__ import annotations

from aiohttp import web

from ..service import ValidationError, agents
from .common import (
    bool_field,
    json_object,
    json_response,
    optional_string_field,
    start,
    string_field,
)


async def trigger(request: web.Request) -> web.Response:
    body = await json_object(request)
    return json_response(
        agents.trigger_call(
            scenario=string_field(body, "scenario", agents.DEFAULT_SCENARIO),
            number=string_field(body, "number"),
        )
    )


async def call(request: web.Request) -> web.Response:
    body = await json_object(request)
    scenario = string_field(body, "scenario", agents.DEFAULT_SCENARIO)
    number = string_field(body, "number")
    timeout = body.get("timeout", 300)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValidationError("timeout must be a positive number of seconds")
    return start(
        request,
        "agent-call",
        f"Agent call: {scenario}",
        {"scenario": scenario, "number": number, "timeout": timeout},
        lambda log: agents.call_and_wait(
            scenario=scenario, number=number, timeout=float(timeout), log=log
        ),
    )


async def record(request: web.Request) -> web.Response:
    return json_response(agents.call_record(call_id=request.match_info["call_id"]))


async def room(request: web.Request) -> web.Response:
    body = await json_object(request)
    scenario = optional_string_field(body, "scenario")
    if scenario and ("/" in scenario or "\\" in scenario):
        raise ValidationError("scenario must be a catalog name")
    every = bool_field(body, "all")
    name_filter = string_field(body, "filter")
    return start(
        request,
        "agent-room",
        f"LiveKit room: {scenario or 'all scenarios'}",
        {"scenario": scenario, "all": every, "filter": name_filter},
        lambda log: agents.run_rooms(
            scenario=scenario, all_scenarios=every, filter=name_filter, log=log
        ),
    )


def register(app: web.Application) -> None:
    app.router.add_post("/api/agents/trigger", trigger)
    app.router.add_post("/api/agents/calls", call)
    app.router.add_get("/api/agents/calls/{call_id}", record)
    app.router.add_post("/api/agents/rooms", room)
