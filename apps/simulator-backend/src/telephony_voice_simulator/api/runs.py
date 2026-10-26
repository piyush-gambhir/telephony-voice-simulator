"""Runs and IVR directory simulations."""

from __future__ import annotations

from aiohttp import web

from .common import json_object, optional_string_field, service_of, string_field


async def list_runs(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).list_runs())


async def get_run(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).get_run(request.match_info["run_id"]))


async def cancel_run(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).cancel_run(request.match_info["run_id"]))


async def create_run(request: web.Request) -> web.Response:
    body = await json_object(request)
    result = await service_of(request).create_run(
        string_field(body, "endpoint_id"),
        optional_string_field(body, "scenario"),
    )
    return web.json_response(result, status=201)


async def create_ivr_simulation(request: web.Request) -> web.Response:
    body = await json_object(request)
    result = service_of(request).create_ivr_simulation(
        endpoint_id=string_field(body, "endpoint_id"),
        caller_number=string_field(body, "caller_number"),
        extension=string_field(body, "extension"),
        disposition=string_field(body, "disposition"),
    )
    return web.json_response(result, status=201)


def register(app: web.Application) -> None:
    app.router.add_get("/api/runs", list_runs)
    app.router.add_post("/api/runs", create_run)
    app.router.add_get("/api/runs/{run_id}", get_run)
    app.router.add_post("/api/runs/{run_id}/cancel", cancel_run)
    app.router.add_post("/api/ivr/simulations", create_ivr_simulation)
