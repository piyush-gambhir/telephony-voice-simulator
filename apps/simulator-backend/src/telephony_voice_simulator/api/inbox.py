"""Controlled SMS inbox, synthetic customer personas and sessions, transcript analysis."""

from __future__ import annotations

import asyncio

from aiohttp import web

from ..service import ValidationError, analysis, customer, sms
from .common import json_object, json_response, service_of, start


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


async def customer_session(request: web.Request) -> web.Response:
    return json_response(
        customer.get_session(service_of(request).store, request.match_info["call_id"])
    )


async def customer_personas(_request: web.Request) -> web.Response:
    return json_response(customer.list_personas())


async def customer_persona(request: web.Request) -> web.Response:
    return json_response(customer.get_persona(request.match_info["persona_id"]))


async def customer_queue(request: web.Request) -> web.Response:
    store = service_of(request).store
    return json_response(customer.list_queue(store, request.query.get("number") or None))


def _persona_source(body: dict) -> tuple[str | None, object]:
    persona_id, inline = body.get("persona_id"), body.get("persona")
    if persona_id is not None and not isinstance(persona_id, str):
        raise ValidationError("persona_id must be a string")
    return persona_id, inline


async def customer_enqueue(request: web.Request) -> web.Response:
    body = await json_object(request)
    persona_id, inline = _persona_source(body)
    number, label = body.get("number"), body.get("label")
    if not isinstance(number, str) or (label is not None and not isinstance(label, str)):
        raise ValidationError("number must be a string, and label a string when given")
    # Queueing a new variant persona provisions an ElevenLabs agent: blocking HTTP.
    queued = await asyncio.to_thread(
        customer.queue_persona, service_of(request).store, number,
        persona_id=persona_id, inline=inline, label=label,
    )
    return json_response(queued, status=201)


async def customer_unqueue(request: web.Request) -> web.Response:
    return json_response(
        customer.cancel_queued(service_of(request).store, request.match_info["queue_id"])
    )


async def customer_grade(request: web.Request) -> web.Response:
    """Grading waits on ElevenLabs test runs, so it runs as an operation."""
    call_id = request.match_info["call_id"]
    body = await json_object(request)
    wait = body.get("wait", False)
    if not isinstance(wait, bool):
        raise ValidationError("wait must be true or false")
    store = service_of(request).store
    customer.get_session(store, call_id)
    return start(
        request, "customer_grade", f"Grade {call_id}", {"call_id": call_id, "wait": wait},
        lambda progress: customer.grade_session(store, call_id, wait=wait, progress=progress),
    )


async def customer_check_persona(request: web.Request) -> web.Response:
    body = await json_object(request)
    persona_id, inline = _persona_source(body)
    repeat = body.get("repeat", 1)
    if not isinstance(repeat, int) or isinstance(repeat, bool):
        raise ValidationError("repeat must be a whole number")
    persona = customer.resolve_persona(persona_id, inline)
    return start(
        request, "customer_check_persona", f"Check persona {persona['id']}",
        {"persona_id": persona["id"], "repeat": repeat},
        lambda progress: customer.check_persona(persona, repeat=repeat, progress=progress),
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
    app.router.add_get("/api/customer/sessions/{call_id}", customer_session)
    app.router.add_post("/api/customer/sessions/{call_id}/refresh", customer_refresh)
    app.router.add_post("/api/customer/sessions/{call_id}/grade", customer_grade)
    app.router.add_get("/api/customer/personas", customer_personas)
    app.router.add_post("/api/customer/personas/check", customer_check_persona)
    app.router.add_get("/api/customer/personas/{persona_id}", customer_persona)
    app.router.add_get("/api/customer/queue", customer_queue)
    app.router.add_post("/api/customer/queue", customer_enqueue)
    app.router.add_delete("/api/customer/queue/{queue_id}", customer_unqueue)
    app.router.add_post("/api/customer/agent/sync", customer_sync_agent)
    app.router.add_post("/api/analysis/silence", classify_silence)
