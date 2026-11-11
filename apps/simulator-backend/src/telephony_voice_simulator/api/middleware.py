"""Translate application errors into control API responses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aiohttp import web

from ..service import ConflictError, NotFoundError, ValidationError


@web.middleware
async def errors(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    try:
        return await handler(request)
    except (ValidationError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except NotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ConflictError as exc:
        return web.json_response({"error": str(exc)}, status=409)
