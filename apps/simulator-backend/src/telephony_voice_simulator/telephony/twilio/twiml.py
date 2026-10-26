"""TwiML response builders."""

from __future__ import annotations

import os
from xml.sax.saxutils import escape

from aiohttp import web


def public_url(path: str) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}{path}"


def twiml(*verbs: str) -> web.Response:
    body = '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(verbs) + "</Response>"
    return web.Response(text=body, content_type="text/xml")


def play(audio_file: str) -> str:
    return f"<Play>{escape(public_url('/assets/' + audio_file))}</Play>"
