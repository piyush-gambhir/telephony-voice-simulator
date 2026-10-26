"""Built-in IVR journeys and PBX stories, run without a line or a database."""

from __future__ import annotations

from aiohttp import web

from ..service import models
from .common import json_object, optional_string_field, string_field, string_map_field


async def simulate(request: web.Request) -> web.Response:
    body = await json_object(request)
    return web.json_response(
        models.simulate_model(
            string_field(body, "kind", "ivr"),
            optional_string_field(body, "scenario"),
            string_map_field(body, "overrides") or None,
        )
    )


async def pbx_extensions(_request: web.Request) -> web.Response:
    return web.json_response(
        {"extensions": models.pbx_extensions(), "states": list(models.EXTENSION_STATES)}
    )


def register(app: web.Application) -> None:
    app.router.add_post("/api/models/simulate", simulate)
    app.router.add_get("/api/models/pbx-extensions", pbx_extensions)
