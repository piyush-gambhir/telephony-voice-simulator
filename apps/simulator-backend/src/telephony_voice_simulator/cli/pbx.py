"""Dealer-group PBX model: dial plan, call routing, transfers, cases and capacity."""

from __future__ import annotations

import argparse

from ..service import pbx
from .common import action, emit, group, pairs


def _call(args: argparse.Namespace) -> int:
    occupied = {
        trunk: int(count) for trunk, count in pairs(args.occupied, "--occupied").items()
    }
    return emit(
        pbx.route_call(
            to_number=args.to,
            from_number=args.from_number,
            day=args.day,
            time=args.time,
            date=args.date,
            digits=args.digits,
            intent=args.intent,
            patience_seconds=args.patience,
            presence=pairs(args.presence, "--presence"),
            occupied_channels=occupied,
            glare_trunks=args.glare,
            transfer_method=args.transfer_method,
            transfer_target=args.transfer_target,
            transfer_agent=args.transfer_agent,
        )
    )


def _suite(_args: argparse.Namespace) -> int:
    report = pbx.run_all_cases()
    emit(report)
    return 0 if report["passed"] == report["total"] else 1


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "pbx", "dealer-group PBX model; places no calls")
    action(actions, "map", "dial plan, trunks, DIDs, agents, transfer matrix",
           lambda _a: emit(pbx.dealer_map()))
    call = action(actions, "call", "route one call through the group", _call)
    call.add_argument("--to", required=True, help="dialed number, E.164")
    call.add_argument("--from", dest="from_number", default="+14085550131")
    call.add_argument("--day", default="tue", choices=pbx.WEEKDAYS)
    call.add_argument("--time", default="10:30", help="local time as HH:MM")
    call.add_argument("--date", default=None, help="MM-DD, to hit a holiday branch")
    call.add_argument("--digits", action="append", default=[], metavar="ENTRY",
                      help="DTMF entry per menu level; repeat for each level")
    call.add_argument("--intent", default="")
    call.add_argument("--patience", type=int, default=600, help="hold tolerance, seconds")
    call.add_argument("--presence", action="append", default=[], metavar="EXT=STATE",
                      help="station state: " + "|".join(pbx.PRESENCE_STATES))
    call.add_argument("--occupied", action="append", default=[], metavar="TRUNK=N",
                      help="channels already in use on a trunk")
    call.add_argument("--glare", action="append", default=[], metavar="TRUNK",
                      help="force a seize collision on a tie trunk")
    call.add_argument("--transfer-method", choices=pbx.TRANSFER_METHODS,
                      help="after answer, have an agent attempt this transfer")
    call.add_argument("--transfer-target", help="destination ref, e.g. ext:1200")
    call.add_argument("--transfer-agent", default="bdc-overflow")
    action(actions, "cases", "named cases", lambda _a: emit(pbx.list_cases()))
    case = action(actions, "case", "run one named case",
                  lambda a: emit(pbx.run_named_case(a.case_id)))
    case.add_argument("case_id")
    action(actions, "suite", "run every case and report pass/fail", _suite)
    capacity = action(
        actions,
        "capacity",
        "concurrent agent calls per integration pattern",
        lambda a: emit(pbx.site_capacity(a.site, a.channels)),
    )
    capacity.add_argument("--site", help="site id from 'pbx map'; omit to compare all sites")
    capacity.add_argument("--channels", type=int, help="override the trunk size")
