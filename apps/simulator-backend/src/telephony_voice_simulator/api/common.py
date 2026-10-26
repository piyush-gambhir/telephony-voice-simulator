"""Request helpers shared by the API route modules."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from ..paths import RESULTS_DIR, UAT_CONFIG_DIR
from ..service import NotFoundError, SimulatorService, ValidationError

SERVICE_KEY = web.AppKey("simulator_service", SimulatorService)
CALL_EVICTOR_KEY = web.AppKey("simulator_call_evictor", Callable[[str], None])


def service_of(request: web.Request) -> SimulatorService:
    return request.app[SERVICE_KEY]


async def json_object(request: web.Request) -> dict[str, object]:
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object")
    return body


def string_field(body: dict[str, object], key: str, default: str = "") -> str:
    value = body.get(key, default)
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a string")
    return value


def optional_string_field(body: dict[str, object], key: str) -> str | None:
    if body.get(key) is None:
        return None
    return string_field(body, key) or None


def optional_int_field(body: dict[str, object], key: str) -> int | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{key} must be a whole number")
    return value


def bool_field(body: dict[str, object], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ValidationError(f"{key} must be true or false")
    return value


def string_list_field(body: dict[str, object], key: str) -> list[str]:
    value = body.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError(f"{key} must be a list of strings")
    return value


def string_map_field(body: dict[str, object], key: str) -> dict[str, str]:
    value = body.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValidationError(f"{key} must be an object of strings")
    return value


def json_response(value: Any, status: int = 200) -> web.Response:
    """JSON response that also serializes paths and dates the service may return."""
    return web.json_response(value, status=status, dumps=lambda data: json.dumps(data, default=str))


def _bare_name(name: str, label: str) -> str:
    # Browser input never names server paths: only files in one known directory.
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValidationError(f"{label} must be a file name, not a path")
    return name


def config_file(name: str) -> Path:
    """A YAML file in the backend config directory, by bare name."""
    path = UAT_CONFIG_DIR / _bare_name(name, "config")
    if path.suffix not in {".yaml", ".yml"} or not path.is_file():
        raise NotFoundError(f"Unknown config: {name}")
    return path


NUMBER_BACKUP_DIR = RESULTS_DIR / "twilio"


def backup_file(name: str) -> Path:
    """A number backup written by inventory point, by bare name."""
    path = NUMBER_BACKUP_DIR / _bare_name(name, "backup")
    if path.suffix != ".json":
        raise ValidationError("backup must be a .json file name")
    return path


def start(
    request: web.Request,
    kind: str,
    title: str,
    params: dict[str, Any],
    job: Callable[[Callable[[str], None]], Any],
) -> web.Response:
    """Run ``job`` as a background operation and answer 202 with the operation."""
    operation = service_of(request).start_operation(kind, title, params, job)
    return json_response(operation, status=202)
