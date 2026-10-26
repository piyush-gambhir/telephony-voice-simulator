"""Runs: dispatch a scenario to a line, IVR directory simulations, cancellation."""

from __future__ import annotations

import argparse

from .common import action, emit, group, run, service

DISPOSITIONS = ("connected", "busy", "no_answer", "failed", "abandoned")


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "runs", "scenario runs on lines")
    action(actions, "list", "recent runs", lambda _a: emit(service().list_runs()))
    show = action(actions, "show", "one run", lambda a: emit(service().get_run(a.run_id)))
    show.add_argument("run_id")
    create = action(
        actions,
        "create",
        "run a scenario on a line; Twilio lines queue it for the next inbound call",
        lambda a: emit(run(service().create_run(a.endpoint_id, a.scenario))),
    )
    create.add_argument("endpoint_id")
    create.add_argument("scenario", nargs="?", help="defaults to the line's scenario")
    cancel = action(
        actions,
        "cancel",
        "cancel a queued run before its call arrives",
        lambda a: emit(service().cancel_run(a.run_id)),
    )
    cancel.add_argument("run_id")
    ivr = action(
        actions,
        "ivr",
        "simulate a caller dialing an extension through the directory",
        lambda a: emit(
            service().create_ivr_simulation(
                endpoint_id=a.endpoint_id,
                caller_number=a.caller_number,
                extension=a.extension,
                disposition=a.disposition,
            )
        ),
    )
    ivr.add_argument("endpoint_id")
    ivr.add_argument("caller_number")
    ivr.add_argument("extension")
    ivr.add_argument("--disposition", choices=DISPOSITIONS, default="connected")
