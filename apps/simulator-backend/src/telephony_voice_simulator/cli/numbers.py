"""Twilio numbers: managed AMD numbers, plus the whole account's inventory."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..service import inventory
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


def _point(args: argparse.Namespace) -> int:
    return emit(
        run(
            inventory.point_number(
                number=args.number,
                route=args.route,
                base_url=args.base_url,
                voice_url=args.voice_url,
                status_callback_url=args.status_callback_url,
                backup=args.backup,
                dry_run=args.dry_run,
            )
        )
    )


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
    action(
        actions,
        "inventory",
        "every number on the Twilio account and its voice URL",
        lambda _a: emit(run(inventory.list_account_numbers())),
    )
    point = action(
        actions,
        "point",
        "point one account number at a simulator route, saving a backup (live Twilio change)",
        _point,
    )
    point.add_argument("--number", required=True, help="E.164 number")
    point.add_argument("--route", choices=tuple(inventory.ROUTES), default="ivr")
    point.add_argument("--base-url", default=os.environ.get("PUBLIC_BASE_URL", ""))
    point.add_argument("--voice-url", help="explicit webhook URL, including Serverless URLs")
    point.add_argument("--status-callback-url")
    point.add_argument("--backup", type=Path, help="default: results/twilio/<sid>.json")
    point.add_argument("--dry-run", action="store_true", help="show the change; write nothing")
    revert = action(
        actions,
        "revert",
        "restore one number from a 'point' backup (live Twilio change)",
        lambda a: emit(
            run(
                inventory.restore_number(
                    number=a.number, backup=a.backup, dry_run=a.dry_run
                )
            )
        ),
    )
    revert.add_argument("--number", required=True)
    revert.add_argument("--backup", type=Path, required=True)
    revert.add_argument("--dry-run", action="store_true")
