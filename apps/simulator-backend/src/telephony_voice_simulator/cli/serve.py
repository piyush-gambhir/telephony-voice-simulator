"""telephony-voice-sim serve: the HTTP API, optionally with Twilio webhooks."""

from __future__ import annotations

import argparse
import os

from aiohttp import web

from ..api.app import build_app
from ..config import validate_serve_configuration
from .common import service


def _serve(args: argparse.Namespace) -> int:
    if args.with_pstn or args.with_ivr:
        validate_serve_configuration(args.host, include_amd=args.with_pstn)
    web.run_app(
        build_app(service(), include_twilio=args.with_pstn, include_ivr=args.with_ivr),
        host=args.host,
        port=args.port,
    )
    return 0


def register(commands: argparse._SubParsersAction) -> None:
    serve = commands.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default=os.environ.get("SIMULATOR_BIND_HOST", "127.0.0.1"))
    serve.add_argument(
        "--port", type=int, default=int(os.environ.get("SIMULATOR_BIND_PORT", "8978"))
    )
    mode = serve.add_mutually_exclusive_group()
    mode.add_argument(
        "--with-pstn",
        action="store_true",
        help="load AMD and IVR Twilio webhooks; requires the private AMD corpus",
    )
    mode.add_argument(
        "--with-ivr",
        action="store_true",
        help="load only the live Twilio IVR; no AMD audio corpus required",
    )
    serve.set_defaults(handler=_serve)
