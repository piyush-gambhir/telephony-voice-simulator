"""Managed Twilio numbers."""

from __future__ import annotations

from aiohttp import web

from ..service import ValidationError
from .common import json_object, optional_string_field, service_of, string_field


async def list_amd_numbers(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).list_amd_numbers())


async def sync_amd_numbers(request: web.Request) -> web.Response:
    return web.json_response(await service_of(request).sync_amd_numbers())


async def update_amd_number(request: web.Request) -> web.Response:
    body = await json_object(request)
    for key in ("friendly_name", "routing_mode"):
        if key in body:
            string_field(body, key)
    if "default_scenario" in body:
        optional_string_field(body, "default_scenario")
    for key in ("enabled", "record_full_calls"):
        if key in body and not isinstance(body[key], bool):
            raise ValidationError(f"{key} must be a boolean")
    kwargs: dict[str, object] = {}
    for key in (
        "friendly_name",
        "default_scenario",
        "routing_mode",
        "enabled",
        "record_full_calls",
    ):
        if key in body:
            kwargs[key] = body[key]
    return web.json_response(
        await service_of(request).update_amd_number(
            request.match_info["number_id"],
            **kwargs,
        )
    )


async def attach_amd_number(request: web.Request) -> web.Response:
    return web.json_response(
        await service_of(request).attach_amd_number(request.match_info["number_id"])
    )


async def restore_amd_number(request: web.Request) -> web.Response:
    return web.json_response(
        await service_of(request).restore_amd_number(request.match_info["number_id"])
    )


async def queue_amd_number(request: web.Request) -> web.Response:
    body = await json_object(request)
    scenario = string_field(body, "scenario").strip()
    if not scenario:
        raise ValidationError("Select an AMD scenario.")
    return web.json_response(
        await service_of(request).queue_amd_number(
            request.match_info["number_id"],
            scenario,
        ),
        status=201,
    )


def register(app: web.Application) -> None:
    app.router.add_get("/api/amd-numbers", list_amd_numbers)
    app.router.add_post("/api/amd-numbers/sync", sync_amd_numbers)
    app.router.add_patch("/api/amd-numbers/{number_id}", update_amd_number)
    app.router.add_post("/api/amd-numbers/{number_id}/attach", attach_amd_number)
    app.router.add_post("/api/amd-numbers/{number_id}/restore", restore_amd_number)
    app.router.add_post("/api/amd-numbers/{number_id}/queue", queue_amd_number)
