"""Twilio-hosted callee and the Twilio account's numbers. These act on live Twilio."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from ..service import hosted, inventory
from .common import (
    NUMBER_BACKUP_DIR,
    backup_file,
    bool_field,
    json_object,
    json_response,
    optional_int_field,
    optional_string_field,
    start,
    string_field,
    string_map_field,
)


async def deploy(request: web.Request) -> web.Response:
    body = await json_object(request)
    static_map = string_map_field(body, "static_map")
    default_scenario = string_field(body, "default_scenario", hosted.DEFAULT_SCENARIO)
    acknowledged = bool_field(body, "acknowledge_public_audio")
    allowlist = optional_string_field(body, "audio_deployment_allowlist")
    return start(
        request,
        "hosted-deploy",
        "Deploy the hosted callee",
        {"default_scenario": default_scenario, "numbers": sorted(static_map)},
        lambda log: hosted.deploy(
            static_map=static_map,
            default_scenario=default_scenario,
            acknowledge_public_audio=acknowledged,
            audio_deployment_allowlist=Path(allowlist) if allowlist else None,
            log=log,
        ),
    )


async def queue(request: web.Request) -> web.Response:
    body = await json_object(request)
    return json_response(
        hosted.queue_scenario(
            number=string_field(body, "number"), scenario=string_field(body, "scenario")
        )
    )


async def smoke(request: web.Request) -> web.Response:
    body = await json_object(request)
    number = string_field(body, "number")
    scenario = string_field(body, "scenario")
    from_number = optional_string_field(body, "from_number")
    return start(
        request,
        "hosted-smoke",
        f"Smoke call to {number}",
        {"number": number, "scenario": scenario},
        lambda log: hosted.smoke_call(
            number=number, scenario=scenario, from_number=from_number, log=log
        ),
    )


async def grade(request: web.Request) -> web.Response:
    body = await json_object(request)
    call_sid = optional_string_field(body, "call_sid")
    number = optional_string_field(body, "number")
    last = optional_int_field(body, "last") or 3
    return start(
        request,
        "hosted-grade",
        f"Grade {call_sid or number}",
        {"call_sid": call_sid, "number": number, "last": last},
        lambda log: hosted.grade_calls(call_sid=call_sid, number=number, last=last, log=log),
    )


async def agent_call(request: web.Request) -> web.Response:
    body = await json_object(request)
    scenario = string_field(body, "scenario", hosted.DEFAULT_SCENARIO)
    queue_first = bool_field(body, "queue", True)
    wait = bool_field(body, "wait", True)
    return start(
        request,
        "hosted-agent-call",
        f"LiveKit agent call: {scenario}",
        {"scenario": scenario, "queue": queue_first, "wait": wait},
        lambda log: hosted.agent_call(scenario=scenario, queue=queue_first, wait=wait, log=log),
    )


async def list_account_numbers(_request: web.Request) -> web.Response:
    return json_response(await inventory.list_account_numbers())


async def list_backups(_request: web.Request) -> web.Response:
    files = sorted(NUMBER_BACKUP_DIR.glob("*.json")) if NUMBER_BACKUP_DIR.is_dir() else []
    return json_response([{"name": path.name, "modified": path.stat().st_mtime} for path in files])


async def point(request: web.Request) -> web.Response:
    body = await json_object(request)
    backup = optional_string_field(body, "backup")
    return json_response(
        await inventory.point_number(
            number=string_field(body, "number"),
            route=string_field(body, "route", "ivr"),
            base_url=string_field(body, "base_url"),
            voice_url=optional_string_field(body, "voice_url"),
            status_callback_url=optional_string_field(body, "status_callback_url"),
            backup=backup_file(backup) if backup else None,
            dry_run=bool_field(body, "dry_run"),
        )
    )


async def revert(request: web.Request) -> web.Response:
    body = await json_object(request)
    return json_response(
        await inventory.restore_number(
            number=string_field(body, "number"),
            backup=backup_file(string_field(body, "backup")),
            dry_run=bool_field(body, "dry_run"),
        )
    )


def register(app: web.Application) -> None:
    app.router.add_post("/api/hosted/deploy", deploy)
    app.router.add_post("/api/hosted/queue", queue)
    app.router.add_post("/api/hosted/smoke", smoke)
    app.router.add_post("/api/hosted/grade", grade)
    app.router.add_post("/api/hosted/agent-call", agent_call)
    app.router.add_get("/api/account-numbers", list_account_numbers)
    app.router.add_get("/api/account-numbers/backups", list_backups)
    app.router.add_post("/api/account-numbers/point", point)
    app.router.add_post("/api/account-numbers/revert", revert)
