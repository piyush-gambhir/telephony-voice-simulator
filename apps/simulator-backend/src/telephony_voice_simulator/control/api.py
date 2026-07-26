"""Optional local HTTP API for the simulator control plane."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

from aiohttp import web

from .service import NotFoundError, SimulatorService, ValidationError

SERVICE_KEY = web.AppKey("simulator_service", SimulatorService)
CALL_EVICTOR_KEY = web.AppKey("simulator_call_evictor", Callable[[str], None])


def _allowed_web_origins() -> set[str]:
    return {
        origin.strip()
        for origin in os.environ.get(
            "SIMULATOR_WEB_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    }


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


@web.middleware
async def cors(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    if request.method == "OPTIONS":
        response: web.StreamResponse = web.Response(status=204)
    else:
        response = await handler(request)
    allowed = _allowed_web_origins()
    origin = request.headers.get("Origin")
    if origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PATCH,DELETE,OPTIONS"
    return response


def _service(request: web.Request) -> SimulatorService:
    return request.app[SERVICE_KEY]


async def _json_object(request: web.Request) -> dict[str, object]:
    body = await request.json()
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object")
    return body


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "telephony-voice-simulator"})


async def provider_catalog(request: web.Request) -> web.Response:
    return web.json_response(_service(request).provider_catalog())


async def list_amd_numbers(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_amd_numbers())


async def sync_amd_numbers(request: web.Request) -> web.Response:
    return web.json_response(await _service(request).sync_amd_numbers())


async def update_amd_number(request: web.Request) -> web.Response:
    body = await _json_object(request)
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
        await _service(request).update_amd_number(
            request.match_info["number_id"],
            **kwargs,
        )
    )


async def attach_amd_number(request: web.Request) -> web.Response:
    return web.json_response(
        await _service(request).attach_amd_number(request.match_info["number_id"])
    )


async def restore_amd_number(request: web.Request) -> web.Response:
    return web.json_response(
        await _service(request).restore_amd_number(request.match_info["number_id"])
    )


async def queue_amd_number(request: web.Request) -> web.Response:
    body = await _json_object(request)
    scenario = str(body.get("scenario") or "").strip()
    if not scenario:
        raise ValidationError("Select an AMD scenario.")
    return web.json_response(
        await _service(request).queue_amd_number(
            request.match_info["number_id"],
            scenario,
        ),
        status=201,
    )


async def list_connections(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_connections())


async def create_connection(request: web.Request) -> web.Response:
    body = await _json_object(request)
    settings = body.get("settings", {})
    if not isinstance(settings, dict):
        raise ValidationError("Provider settings must be a JSON object")
    result = _service(request).create_connection(
        str(body.get("provider", "")),
        str(body.get("name", "")),
        settings,
        status=str(body.get("status", "ready")),
        description=str(body.get("description", "")),
    )
    return web.json_response(result, status=201)


async def update_connection(request: web.Request) -> web.Response:
    body = await _json_object(request)
    if "provider" in body:
        raise ValidationError(
            "Provider type cannot be changed; create a new provider connection instead"
        )
    values: dict[str, object] = {}
    for key in ("name", "status", "description"):
        if key not in body:
            continue
        if not isinstance(body[key], str):
            raise ValidationError(f"{key} must be a string")
        values[key] = body[key]
    if "settings" in body:
        if not isinstance(body["settings"], dict):
            raise ValidationError("Provider settings must be a JSON object")
        values["settings"] = body["settings"]
    result = _service(request).update_connection(
        request.match_info["connection_id"],
        **values,
    )
    return web.json_response(result)


async def delete_connection(request: web.Request) -> web.Response:
    _service(request).delete_connection(request.match_info["connection_id"])
    return web.Response(status=204)


async def list_endpoints(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_endpoints())


async def create_endpoint(request: web.Request) -> web.Response:
    body = await _json_object(request)
    result = _service(request).create_endpoint(
        connection_id=str(body.get("connection_id", "")),
        name=str(body.get("name", "")),
        kind=str(body.get("kind", "phone_number")),
        address=str(body.get("address", "")),
        routing_mode=str(body.get("routing_mode", "fixed")),
        default_scenario=str(body.get("default_scenario") or "") or None,
    )
    return web.json_response(result, status=201)


async def update_endpoint(request: web.Request) -> web.Response:
    body = await _json_object(request)
    for key in ("connection_id", "name", "kind", "address", "routing_mode"):
        if key in body and not isinstance(body[key], str):
            raise ValidationError(f"{key} must be a string")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise ValidationError("enabled must be a boolean")
    if "default_scenario" in body and body["default_scenario"] is not None:
        if not isinstance(body["default_scenario"], str):
            raise ValidationError("default_scenario must be a string or null")
    values: dict[str, object] = {
        key: body[key]
        for key in (
            "connection_id",
            "name",
            "kind",
            "address",
            "routing_mode",
            "enabled",
        )
        if key in body
    }
    if "default_scenario" in body:
        values["default_scenario"] = body["default_scenario"]
    result = _service(request).update_endpoint(
        request.match_info["endpoint_id"],
        **values,
    )
    return web.json_response(result)


async def delete_endpoint(request: web.Request) -> web.Response:
    _service(request).delete_endpoint(request.match_info["endpoint_id"])
    return web.Response(
        status=204,
        headers={
            "Warning": (
                '299 telephony-voice-simulator "Deleting an endpoint does not '
                'detach or reconfigure the carrier number"'
            )
        },
    )


async def list_directory(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_directory_entries())


async def create_directory_entry(request: web.Request) -> web.Response:
    body = await _json_object(request)
    result = _service(request).create_directory_entry(
        connection_id=str(body.get("connection_id", "")),
        extension=str(body.get("extension", "")),
        name=str(body.get("name", "")),
        destination=str(body.get("destination", "")),
        department=str(body.get("department", "General")),
        ring_timeout=body.get("ring_timeout", 25),
    )
    return web.json_response(result, status=201)


async def update_directory_entry(request: web.Request) -> web.Response:
    body = await _json_object(request)
    for key in ("connection_id", "extension", "name", "destination", "department"):
        if key in body and not isinstance(body[key], str):
            raise ValidationError(f"{key} must be a string")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise ValidationError("enabled must be a boolean")
    result = _service(request).update_directory_entry(
        request.match_info["entry_id"],
        connection_id=body.get("connection_id"),
        extension=body.get("extension"),
        name=body.get("name"),
        destination=body.get("destination"),
        department=body.get("department"),
        ring_timeout=body.get("ring_timeout"),
        enabled=body.get("enabled"),
    )
    return web.json_response(result)


async def delete_directory_entry(request: web.Request) -> web.Response:
    _service(request).delete_directory_entry(request.match_info["entry_id"])
    return web.Response(status=204)


async def list_scenarios(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_scenarios())


async def list_runs(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_runs())


async def list_calls(request: web.Request) -> web.Response:
    return web.json_response(_service(request).list_calls())


async def recording_media(request: web.Request) -> web.StreamResponse:
    path = _service(request).recording_path(request.match_info["recording_id"])
    return web.FileResponse(path, headers={"Content-Type": "audio/wav"})


async def create_run(request: web.Request) -> web.Response:
    body = await _json_object(request)
    result = await _service(request).create_run(
        str(body.get("endpoint_id", "")),
        str(body.get("scenario") or "") or None,
    )
    return web.json_response(result, status=201)


async def create_ivr_simulation(request: web.Request) -> web.Response:
    body = await _json_object(request)
    result = _service(request).create_ivr_simulation(
        endpoint_id=str(body.get("endpoint_id", "")),
        caller_number=str(body.get("caller_number", "")),
        extension=str(body.get("extension", "")),
        disposition=str(body.get("disposition", "")),
    )
    return web.json_response(result, status=201)


async def create_legacy_simulation(request: web.Request) -> web.Response:
    """Compatibility adapter for the original telephony-simulator console."""

    body = await _json_object(request)
    service = _service(request)
    endpoint_id = str(body.get("endpoint_id") or body.get("endpointId") or "")
    if not endpoint_id:
        endpoint_id = next(
            (
                endpoint.id
                for endpoint in service.store.list_endpoints()
                if endpoint.enabled
                and endpoint.kind == "phone_number"
                and (
                    (connection := service.store.get_connection(endpoint.connection_id))
                    is not None
                )
                and connection.enabled
            ),
            "",
        )
    if not endpoint_id:
        raise ValidationError(
            "Create an enabled endpoint before using the deprecated /api/simulations route"
        )
    result = service.create_ivr_simulation(
        endpoint_id=endpoint_id,
        caller_number=str(body.get("caller_number") or body.get("callerNumber") or ""),
        extension=str(body.get("extension", "")),
        disposition=str(body.get("disposition", "")),
    )
    steps = [
        {
            "at": step.get("at", ""),
            "actor": step.get("actor", "simulator"),
            "title": step.get("label", step.get("kind", "Simulation event")),
            "detail": step.get("detail", ""),
        }
        for step in result["timeline"]
    ]
    return web.json_response(
        {"run": result, "steps": steps},
        headers={
            "Deprecation": "true",
            "Link": '</api/ivr/simulations>; rel="successor-version"',
        },
    )


async def delete_call(request: web.Request) -> web.Response:
    call_id = request.match_info["call_id"]
    evictor = request.app.get(CALL_EVICTOR_KEY)
    service = _service(request)
    try:
        result = service.delete_call(call_id)
    finally:
        if evictor is not None and service.store.is_incoming_call_deleted(call_id):
            evictor(call_id)
    return web.json_response(result)


async def options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


def register_control_routes(app: web.Application) -> None:
    app.router.add_get("/health", health)
    app.router.add_get("/api/providers/catalog", provider_catalog)
    app.router.add_get("/api/amd-numbers", list_amd_numbers)
    app.router.add_post("/api/amd-numbers/sync", sync_amd_numbers)
    app.router.add_patch("/api/amd-numbers/{number_id}", update_amd_number)
    app.router.add_post("/api/amd-numbers/{number_id}/attach", attach_amd_number)
    app.router.add_post("/api/amd-numbers/{number_id}/restore", restore_amd_number)
    app.router.add_post("/api/amd-numbers/{number_id}/queue", queue_amd_number)
    app.router.add_get("/api/providers", list_connections)
    app.router.add_post("/api/providers", create_connection)
    app.router.add_patch("/api/providers/{connection_id}", update_connection)
    app.router.add_delete("/api/providers/{connection_id}", delete_connection)
    app.router.add_get("/api/endpoints", list_endpoints)
    app.router.add_post("/api/endpoints", create_endpoint)
    app.router.add_patch("/api/endpoints/{endpoint_id}", update_endpoint)
    app.router.add_delete("/api/endpoints/{endpoint_id}", delete_endpoint)
    app.router.add_get("/api/directory", list_directory)
    app.router.add_post("/api/directory", create_directory_entry)
    app.router.add_patch("/api/directory/{entry_id}", update_directory_entry)
    app.router.add_delete("/api/directory/{entry_id}", delete_directory_entry)
    app.router.add_get("/api/scenarios", list_scenarios)
    app.router.add_get("/api/runs", list_runs)
    app.router.add_post("/api/runs", create_run)
    app.router.add_post("/api/ivr/simulations", create_ivr_simulation)
    app.router.add_post("/api/simulations", create_legacy_simulation)
    app.router.add_get("/api/calls", list_calls)
    app.router.add_delete("/api/calls/{call_id}", delete_call)
    app.router.add_get("/api/recordings/{recording_id}/media", recording_media)
    app.router.add_route("OPTIONS", "/{path:.*}", options)


def build_app(
    service: SimulatorService | None = None,
    *,
    include_twilio: bool = False,
    include_ivr: bool = False,
) -> web.Application:
    # CORS is outermost so validation/error responses remain readable by the
    # browser console as well as successful responses.
    app = web.Application(middlewares=[cors, errors])
    app[SERVICE_KEY] = service or SimulatorService()
    register_control_routes(app)
    if include_twilio or include_ivr:
        from ..telephony.providers.twilio import webhooks as pstn_server

        # The live IVR and the console must resolve the exact same directory,
        # including when callers inject a non-default store in tests or embeds.
        pstn_server.CALL_STORE = app[SERVICE_KEY].store
        app[CALL_EVICTOR_KEY] = pstn_server.evict_call
        if include_twilio and not pstn_server.STATE.scenarios:
            pstn_server.COMPILED_DIR.mkdir(parents=True, exist_ok=True)
            pstn_server.STATE.load_scenarios()
        pstn_server.register_routes(
            app,
            include_amd=include_twilio,
            include_ivr=include_twilio or include_ivr,
        )
    return app
