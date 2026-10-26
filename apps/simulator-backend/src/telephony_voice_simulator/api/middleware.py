"""HTTP middleware shared by the control API and the Twilio webhook app."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web

from ..service.simulator import ConflictError, NotFoundError, ValidationError


def _allowed_web_origins() -> set[str]:
    return {
        origin.strip()
        for origin in os.environ.get(
            "SIMULATOR_WEB_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    }


def _origin_allowed(origin: str) -> bool:
    """Configured origins, plus any loopback dev port (the console may run on another port)."""
    if origin in _allowed_web_origins():
        return True
    parts = urlsplit(origin)
    return parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}


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


@web.middleware
async def cors(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    origin = request.headers.get("Origin")
    allowed = origin is not None and _origin_allowed(origin)
    if request.method == "OPTIONS":
        response: web.StreamResponse = web.Response(status=204)
    elif origin is not None and not allowed and request.method not in {"GET", "HEAD"}:
        # CORS only hides responses; refuse cross-site writes (simple POSTs skip preflight).
        response = web.json_response({"error": "Origin not allowed"}, status=403)
    else:
        response = await handler(request)
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PATCH,DELETE,OPTIONS"
    return response
