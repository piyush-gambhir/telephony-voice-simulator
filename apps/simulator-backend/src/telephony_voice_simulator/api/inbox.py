"""Controlled SMS inbox, synthetic customer personas and sessions, transcript analysis."""

from __future__ import annotations

import asyncio

from aiohttp import web

from ..service import ValidationError, analysis, customer, sms
from .common import json_object, json_response, service_of


def _limit(request: web.Request, default: int) -> int:
    raw = request.query.get("limit")
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ValidationError("limit must be a whole number") from exc


async def sms_messages(request: web.Request) -> web.Response:
    return json_response(sms.list_messages(limit=_limit(request, 50)))


async def sms_status(_request: web.Request) -> web.Response:
    return json_response(sms.status())


async def customer_profiles(_request: web.Request) -> web.Response:
    return json_response(customer.profiles())


async def customer_sessions(request: web.Request) -> web.Response:
    return json_response(
        customer.list_sessions(service_of(request).store, limit=_limit(request, 20))
    )


async def customer_refresh(request: web.Request) -> web.Response:
    return json_response(
        await asyncio.to_thread(
            customer.refresh_session, service_of(request).store, request.match_info["call_id"]
        )
    )


async def customer_sync_agent(_request: web.Request) -> web.Response:
    return json_response(await asyncio.to_thread(customer.sync_agent))


async def classify_silence(request: web.Request) -> web.Response:
    """Rows are parsed in the browser from an exported JSONL file."""
    body = await json_object(request)
    rows = body.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValidationError("rows must be a list of {call_uid, user_text} objects")
    if len(rows) > 50_000:
        raise ValidationError("At most 50,000 rows per request")
    return json_response(analysis.classify_silence_rows(rows))


def register(app: web.Application) -> None:
    app.router.add_get("/api/sms/messages", sms_messages)
    app.router.add_get("/api/sms/status", sms_status)
    app.router.add_get("/api/customer/profiles", customer_profiles)
    app.router.add_get("/api/customer/sessions", customer_sessions)
    app.router.add_post("/api/customer/sessions/{call_id}/refresh", customer_refresh)
    app.router.add_post("/api/customer/agent/sync", customer_sync_agent)
    app.router.add_post("/api/analysis/silence", classify_silence)
