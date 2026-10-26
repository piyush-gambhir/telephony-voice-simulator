"""Managed Twilio numbers: sync, configure, attach to the AMD runtime, restore, queue."""

from __future__ import annotations

import argparse

from .common import action, emit, group, on_off, run, service


def _update(args: argparse.Namespace) -> int:
    changes: dict = {
        "friendly_name": args.name,
        "routing_mode": args.routing,
        "enabled": args.enabled,
        "record_full_calls": args.record,
    }
    if args.no_scenario:
        changes["default_scenario"] = None
    elif args.scenario is not None:
        changes["default_scenario"] = args.scenario
    return emit(run(service().update_amd_number(args.number_id, **changes)))


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "numbers", "Twilio numbers managed for the AMD callee")
    action(actions, "list", "managed numbers", lambda _a: emit(service().list_amd_numbers()))
    action(
        actions,
        "sync",
        "adopt the account's simulator numbers",
        lambda _a: emit(run(service().sync_amd_numbers())),
    )
    update = action(actions, "update", "configure a managed number", _update)
    update.add_argument("number_id")
    update.add_argument("--name")
    update.add_argument("--routing", choices=("fixed", "queued"))
    scenario = update.add_mutually_exclusive_group()
    scenario.add_argument("--scenario", help="default scenario")
    scenario.add_argument("--no-scenario", action="store_true", help="clear the default")
    update.add_argument("--enabled", type=on_off, metavar="on|off")
    update.add_argument("--record", type=on_off, metavar="on|off", help="full-call recording")
    attach = action(
        actions,
        "attach",
        "point the number's voice webhook at this simulator (live Twilio change)",
        lambda a: emit(run(service().attach_amd_number(a.number_id))),
    )
    attach.add_argument("number_id")
    restore = action(
        actions,
        "restore",
        "put back the webhook saved before attach (live Twilio change)",
        lambda a: emit(run(service().restore_amd_number(a.number_id))),
    )
    restore.add_argument("number_id")
    queue = action(
        actions,
        "queue",
        "queue a scenario for the number's next inbound call",
        lambda a: emit(run(service().queue_amd_number(a.number_id, a.scenario))),
    )
    queue.add_argument("number_id")
    queue.add_argument("scenario")
