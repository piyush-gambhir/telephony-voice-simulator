"""Calls and recording media."""

from __future__ import annotations

from aiohttp import web

from .common import CALL_EVICTOR_KEY, service_of


async def list_calls(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).list_calls())


async def get_call(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).get_call(request.match_info["call_id"]))


async def recording_media(request: web.Request) -> web.StreamResponse:
    path = service_of(request).recording_path(request.match_info["recording_id"])
    return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


async def delete_call(request: web.Request) -> web.Response:
    call_id = request.match_info["call_id"]
    evictor = request.app.get(CALL_EVICTOR_KEY)
    service = service_of(request)
    try:
        result = service.delete_call(call_id)
    finally:
        if evictor is not None and service.store.is_incoming_call_deleted(call_id):
            evictor(call_id)
    return web.json_response(result)


def register(app: web.Application) -> None:
    app.router.add_get("/api/calls", list_calls)
    app.router.add_get("/api/calls/{call_id}", get_call)
    app.router.add_delete("/api/calls/{call_id}", delete_call)
    app.router.add_get("/api/recordings/{recording_id}/media", recording_media)
