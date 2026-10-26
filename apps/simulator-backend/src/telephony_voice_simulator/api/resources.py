"""Health, provider connections, lines (endpoints), the directory and the scenario catalog."""

from __future__ import annotations

from aiohttp import web

from ..service import ValidationError
from .common import json_object, optional_string_field, service_of, string_field


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "telephony-voice-simulator"})


async def provider_catalog(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).provider_catalog())


async def list_connections(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).list_connections())


async def create_connection(request: web.Request) -> web.Response:
    body = await json_object(request)
    settings = body.get("settings", {})
    if not isinstance(settings, dict):
        raise ValidationError("Provider settings must be a JSON object")
    result = service_of(request).create_connection(
        string_field(body, "provider"),
        string_field(body, "name"),
        settings,
        status=string_field(body, "status", "ready"),
        description=string_field(body, "description"),
    )
    return web.json_response(result, status=201)


async def update_connection(request: web.Request) -> web.Response:
    body = await json_object(request)
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
    result = service_of(request).update_connection(
        request.match_info["connection_id"],
        **values,
    )
    return web.json_response(result)


async def delete_connection(request: web.Request) -> web.Response:
    service_of(request).delete_connection(request.match_info["connection_id"])
    return web.Response(status=204)


async def list_endpoints(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).list_endpoints())


async def create_endpoint(request: web.Request) -> web.Response:
    body = await json_object(request)
    result = service_of(request).create_endpoint(
        connection_id=string_field(body, "connection_id"),
        name=string_field(body, "name"),
        kind=string_field(body, "kind", "phone_number"),
        address=string_field(body, "address"),
        routing_mode=string_field(body, "routing_mode", "fixed"),
        default_scenario=optional_string_field(body, "default_scenario"),
    )
    return web.json_response(result, status=201)


async def update_endpoint(request: web.Request) -> web.Response:
    body = await json_object(request)
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
    result = service_of(request).update_endpoint(
        request.match_info["endpoint_id"],
        **values,
    )
    return web.json_response(result)


async def delete_endpoint(request: web.Request) -> web.Response:
    service_of(request).delete_endpoint(request.match_info["endpoint_id"])
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
    return web.json_response(service_of(request).list_directory_entries())


async def create_directory_entry(request: web.Request) -> web.Response:
    body = await json_object(request)
    result = service_of(request).create_directory_entry(
        connection_id=string_field(body, "connection_id"),
        extension=string_field(body, "extension"),
        name=string_field(body, "name"),
        destination=string_field(body, "destination"),
        department=string_field(body, "department", "General"),
        ring_timeout=body.get("ring_timeout", 25),
    )
    return web.json_response(result, status=201)


async def update_directory_entry(request: web.Request) -> web.Response:
    body = await json_object(request)
    for key in ("connection_id", "extension", "name", "destination", "department"):
        if key in body and not isinstance(body[key], str):
            raise ValidationError(f"{key} must be a string")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise ValidationError("enabled must be a boolean")
    result = service_of(request).update_directory_entry(
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
    service_of(request).delete_directory_entry(request.match_info["entry_id"])
    return web.Response(status=204)


async def list_scenarios(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).list_scenarios())


async def get_scenario(request: web.Request) -> web.Response:
    return web.json_response(service_of(request).get_scenario(request.match_info["name"]))


async def validate_scenarios(request: web.Request) -> web.Response:
    """Validate one AMD scenario by name, or every scenario file when none is given."""
    body = await json_object(request)
    return web.json_response(
        service_of(request).catalog.validate(optional_string_field(body, "scenario"))
    )


def register(app: web.Application) -> None:
    app.router.add_get("/health", health)
    app.router.add_get("/api/providers/catalog", provider_catalog)
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
    app.router.add_post("/api/scenarios/validate", validate_scenarios)
    app.router.add_get("/api/scenarios/{name}", get_scenario)
