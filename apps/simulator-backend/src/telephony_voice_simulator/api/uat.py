"""UAT workflows from the backend config directory."""

from __future__ import annotations

from aiohttp import web

from ..service import uat
from .common import config_file, json_object, json_response, start, string_field, string_list_field


async def list_configs(_request: web.Request) -> web.Response:
    return json_response([{"name": item["name"]} for item in uat.list_configs()])


async def check(request: web.Request) -> web.Response:
    body = await json_object(request)
    return json_response(uat.check_config(config=config_file(string_field(body, "config"))))


async def plan(request: web.Request) -> web.Response:
    body = await json_object(request)
    return json_response(
        uat.plan(
            config=config_file(string_field(body, "config")),
            scenarios=string_list_field(body, "scenarios") or None,
        )
    )


async def run(request: web.Request) -> web.Response:
    body = await json_object(request)
    name = string_field(body, "config")
    config = config_file(name)
    scenarios = string_list_field(body, "scenarios") or None
    uat.check_config(config=config)
    return start(
        request,
        "uat",
        f"UAT: {name}",
        {"config": name, "scenarios": scenarios},
        lambda log: uat.run(config=config, scenarios=scenarios, log=log),
    )


def register(app: web.Application) -> None:
    app.router.add_get("/api/uat/configs", list_configs)
    app.router.add_post("/api/uat/check", check)
    app.router.add_post("/api/uat/plan", plan)
    app.router.add_post("/api/uat/runs", run)
