"""Audio corpus: build, import, validate for public serving, and IVR prompts.

Building and rendering call paid text-to-speech APIs.
"""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from ..service import ValidationError, corpus
from .common import bool_field, json_object, json_response, start, string_field

# Builds and imports both rewrite the shared manifest, so they share one operation
# kind and the runner lets only one of them run at a time.
CORPUS_KIND = "corpus"


def _number(body: dict[str, object], key: str, default: float) -> float:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{key} must be a number")
    return float(value)


async def build(request: web.Request) -> web.Response:
    body = await json_object(request)
    only = string_field(body, "only")
    force = bool_field(body, "force")
    replace_imported = bool_field(body, "replace_imported")
    return start(
        request,
        CORPUS_KIND,
        f"Build corpus{': ' + only if only else ''}",
        {"only": only, "force": force, "replace_imported": replace_imported},
        lambda log: corpus.build_corpus(
            only=only, force=force, replace_imported=replace_imported, log=log
        ),
    )


async def import_audio(request: web.Request) -> web.Response:
    body = await json_object(request)
    params = {
        "asset": string_field(body, "asset"),
        "source": string_field(body, "source"),
        "source_type": string_field(body, "source_type"),
        "license_note": string_field(body, "license_note"),
        "transcript": string_field(body, "transcript"),
        "commit_allowed": bool_field(body, "commit_allowed"),
    }
    if params["source_type"] not in corpus.SOURCE_TYPES:
        raise ValidationError(f"source_type must be one of {', '.join(corpus.SOURCE_TYPES)}")
    return start(
        request,
        CORPUS_KIND,
        f"Import {params['asset']}",
        params,
        lambda _log: corpus.import_audio(**params),
    )


async def import_from_recording(request: web.Request) -> web.Response:
    body = await json_object(request)
    channel = body.get("channel", 1)
    if channel not in (0, 1):
        raise ValidationError("channel must be 0 or 1")
    params = {
        "call_id": string_field(body, "call_id"),
        "asset": string_field(body, "asset"),
        "license_note": string_field(body, "license_note"),
        "channel": channel,
        "start": _number(body, "start", 0.0),
        "duration": _number(body, "duration", 0.0),
        "transcript": string_field(body, "transcript"),
        "commit_allowed": bool_field(body, "commit_allowed"),
    }
    return start(
        request,
        CORPUS_KIND,
        f"Import {params['asset']} from {params['call_id']}",
        params,
        lambda _log: corpus.import_from_recording(**params),
    )


async def validate_public(request: web.Request) -> web.Response:
    body = await json_object(request)
    allowlist = string_field(body, "allowlist")
    if not allowlist:
        raise ValidationError("allowlist must name the reviewed deployment allowlist file")
    return json_response(corpus.validate_public_audio(allowlist=Path(allowlist)))


async def check_prompts(_request: web.Request) -> web.Response:
    return json_response(corpus.check_prompts())


async def render_prompts(request: web.Request) -> web.Response:
    body = await json_object(request)
    engine = string_field(body, "engine", "auto")
    if engine not in corpus.PROMPT_ENGINES:
        raise ValidationError(f"engine must be one of {', '.join(corpus.PROMPT_ENGINES)}")
    force = bool_field(body, "force")
    corpus.check_prompts()
    return start(
        request,
        "prompts-render",
        "Render IVR prompts",
        {"engine": engine, "force": force},
        lambda log: corpus.render_prompts(engine=engine, force=force, log=log),
    )


def register(app: web.Application) -> None:
    app.router.add_post("/api/corpus/build", build)
    app.router.add_post("/api/corpus/import", import_audio)
    app.router.add_post("/api/corpus/import-recording", import_from_recording)
    app.router.add_post("/api/corpus/validate-public", validate_public)
    app.router.add_get("/api/corpus/prompts", check_prompts)
    app.router.add_post("/api/corpus/prompts/render", render_prompts)
