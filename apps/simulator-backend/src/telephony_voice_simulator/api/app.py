"""HTTP control API over the application service."""

from __future__ import annotations

from aiohttp import web

from ..service import SimulatorService
from . import calls, models, numbers, operations, pbx, resources, runs
from .common import CALL_EVICTOR_KEY, SERVICE_KEY
from .middleware import cors, errors

ROUTE_MODULES = (resources, numbers, runs, calls, models, pbx, operations)


async def options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


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
    for module in ROUTE_MODULES:
        module.register(app)
    app.router.add_route("OPTIONS", "/{path:.*}", options)
    if include_twilio or include_ivr:
        from ..telephony.twilio import state, webhooks

        # The live IVR and the console must resolve the exact same directory,
        # including when callers inject a non-default store in tests or embeds.
        state.CALL_STORE = app[SERVICE_KEY].store
        app[CALL_EVICTOR_KEY] = state.evict_call
        if include_twilio and not state.STATE.scenarios:
            state.COMPILED_DIR.mkdir(parents=True, exist_ok=True)
            state.STATE.load_scenarios()
        webhooks.register_routes(
            app,
            include_amd=include_twilio,
            include_ivr=include_twilio or include_ivr,
        )
    return app
