"""Long-running operations: list, inspect, and the shared 202 response."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from .common import SERVICE_KEY, json_response, service_of

RESTART_REASON = "The simulator restarted before this operation finished"


def accepted(operation: dict[str, Any]) -> web.Response:
    """202 with the queued operation; clients poll GET /api/operations/{id}."""
    return json_response(operation, status=202)


async def list_operations(request: web.Request) -> web.Response:
    kind = request.query.get("kind") or None
    return json_response(service_of(request).list_operations(kind=kind))


async def get_operation(request: web.Request) -> web.Response:
    return json_response(service_of(request).get_operation(request.match_info["operation_id"]))


async def _close_interrupted(app: web.Application) -> None:
    app[SERVICE_KEY].store.fail_unfinished_operations(RESTART_REASON)


async def _shutdown(app: web.Application) -> None:
    runner = app[SERVICE_KEY]._runner
    if runner is not None:
        runner.shutdown()


def register(app: web.Application) -> None:
    app.router.add_get("/api/operations", list_operations)
    app.router.add_get("/api/operations/{operation_id}", get_operation)
    app.on_startup.append(_close_interrupted)
    app.on_cleanup.append(_shutdown)
