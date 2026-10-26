"""Twilio webhook routes for the AMD callee and the live IVR.

Point a Twilio number's Voice URL at ``POST /twilio/voice`` (the console's Lines
page does this for managed numbers). The next inbound call answers as the run
queued for that line, or as the line's default scenario.

  POST /twilio/voice         answer webhook: first TwiML segment
  POST /twilio/step          segment continuation (record done, gather result)
  GET  /assets/{file}        compiled scenario audio for <Play>
  POST /twilio/ivr           live directory menu
  POST /twilio/ivr/dial      resolve the gathered extension and bridge
  POST /twilio/ivr/result    persist the bridged leg's result
  POST /twilio/recording     recording status callback: download the audio
  POST /twilio/status        call status callback: finalize and grade
"""

from __future__ import annotations

from aiohttp import web

from ...api.middleware import cors
from .amd import handle_asset, handle_step, handle_voice
from .callbacks import handle_recording, handle_status
from .ivr import handle_ivr_dial, handle_ivr_dial_result, handle_ivr_voice


def register_routes(
    app: web.Application,
    *,
    include_amd: bool = True,
    include_ivr: bool = True,
) -> None:
    from . import finalization

    app.on_startup.append(finalization.startup)
    app.on_cleanup.append(finalization.cleanup)
    if include_amd:
        from .customer import register_routes as register_customer_routes

        register_customer_routes(app)
        from .sms import register_routes as register_sms_routes

        register_sms_routes(app)
        app.router.add_post("/twilio/voice", handle_voice)
        app.router.add_post("/twilio/step", handle_step)
        app.router.add_get("/assets/{file}", handle_asset)
    if include_ivr:
        app.router.add_post("/twilio/ivr", handle_ivr_voice)
        app.router.add_post("/twilio/ivr/dial", handle_ivr_dial)
        app.router.add_post("/twilio/ivr/result", handle_ivr_dial_result)
    # Both runtimes share recording and status callbacks. They are registered
    # once when either mode is active.
    app.router.add_post("/twilio/recording", handle_recording)
    app.router.add_post("/twilio/status", handle_status)


def build_app() -> web.Application:
    """Twilio routes alone, for tests and embedding."""
    app = web.Application(middlewares=[cors])
    register_routes(app)
    return app
