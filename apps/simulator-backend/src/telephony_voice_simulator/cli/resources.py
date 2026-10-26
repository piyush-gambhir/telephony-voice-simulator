"""Provider connections, lines (endpoints) and the extension directory."""

from __future__ import annotations

import argparse
import json

from .common import action, emit, group, on_off, service

PROVIDERS = ("mock", "twilio", "telnyx")
STATUSES = ("ready", "needs_setup", "disabled")
ENDPOINT_KINDS = ("phone_number", "sip_uri", "extension")
ROUTING = ("fixed", "queued")


def _settings(value: str | None) -> dict | None:
    if value is None:
        return None
    try:
        settings = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--settings must be a JSON object: {exc}") from exc
    if not isinstance(settings, dict):
        raise SystemExit("--settings must be a JSON object")
    return settings


def _register_providers(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "providers", "provider connections (Twilio, Telnyx, local mock)")
    action(actions, "list", "list connections", lambda _a: emit(service().list_connections()))
    action(
        actions,
        "catalog",
        "supported providers and their runtime readiness",
        lambda _a: emit(service().provider_catalog()),
    )
    add = action(
        actions,
        "add",
        "create a connection",
        lambda a: emit(
            service().create_connection(
                a.provider,
                a.name,
                _settings(a.settings),
                status=a.status,
                description=a.description,
            )
        ),
    )
    add.add_argument("provider", choices=PROVIDERS)
    add.add_argument("name")
    add.add_argument("--status", choices=STATUSES, default="ready")
    add.add_argument("--description", default="")
    add.add_argument("--settings", help="provider settings as a JSON object")
    update = action(
        actions,
        "update",
        "change a connection",
        lambda a: emit(
            service().update_connection(
                a.connection_id,
                name=a.name,
                status=a.status,
                description=a.description,
                settings=_settings(a.settings),
            )
        ),
    )
    update.add_argument("connection_id")
    update.add_argument("--name")
    update.add_argument("--status", choices=STATUSES)
    update.add_argument("--description")
    update.add_argument("--settings", help="provider settings as a JSON object")
    delete = action(actions, "delete", "delete a connection and its lines", _delete_provider)
    delete.add_argument("connection_id")


def _delete_provider(args: argparse.Namespace) -> int:
    service().delete_connection(args.connection_id)
    return emit({"deleted": args.connection_id})


def _update_endpoint(args: argparse.Namespace) -> int:
    changes: dict = {
        "name": args.name,
        "address": args.address,
        "kind": args.kind,
        "routing_mode": args.routing,
        "enabled": args.enabled,
    }
    if args.no_scenario:
        changes["default_scenario"] = None
    elif args.scenario is not None:
        changes["default_scenario"] = args.scenario
    return emit(service().update_endpoint(args.endpoint_id, **changes))


def _delete_endpoint(args: argparse.Namespace) -> int:
    service().delete_endpoint(args.endpoint_id)
    return emit({"deleted": args.endpoint_id})


def _register_endpoints(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "lines", "lines (endpoints) that agents call")
    action(actions, "list", "list lines", lambda _a: emit(service().list_endpoints()))
    add = action(
        actions,
        "add",
        "create a line",
        lambda a: emit(
            service().create_endpoint(
                connection_id=a.connection_id,
                name=a.name,
                kind=a.kind,
                address=a.address,
                routing_mode=a.routing,
                default_scenario=a.scenario,
            )
        ),
    )
    add.add_argument("connection_id")
    add.add_argument("name")
    add.add_argument("address", help="E.164 number, SIP URI or extension")
    add.add_argument("--kind", choices=ENDPOINT_KINDS, default="phone_number")
    add.add_argument("--scenario", help="default scenario")
    add.add_argument("--routing", choices=ROUTING, default="fixed")
    update = action(actions, "update", "change a line", _update_endpoint)
    update.add_argument("endpoint_id")
    update.add_argument("--name")
    update.add_argument("--address")
    update.add_argument("--kind", choices=ENDPOINT_KINDS)
    update.add_argument("--routing", choices=ROUTING)
    scenario = update.add_mutually_exclusive_group()
    scenario.add_argument("--scenario", help="default scenario")
    scenario.add_argument("--no-scenario", action="store_true", help="clear the default")
    update.add_argument("--enabled", type=on_off, metavar="on|off")
    delete = action(actions, "delete", "delete a line", _delete_endpoint)
    delete.add_argument("endpoint_id")


def _update_extension(args: argparse.Namespace) -> int:
    return emit(
        service().update_directory_entry(
            args.entry_id,
            extension=args.extension,
            name=args.name,
            destination=args.destination,
            department=args.department,
            ring_timeout=args.timeout,
            enabled=args.enabled,
        )
    )


def _delete_extension(args: argparse.Namespace) -> int:
    service().delete_directory_entry(args.entry_id)
    return emit({"deleted": args.entry_id})


def _register_directory(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "directory", "extension directory the live IVR dials")
    action(actions, "list", "list extensions", lambda _a: emit(service().list_directory_entries()))
    add = action(
        actions,
        "add",
        "add an extension",
        lambda a: emit(
            service().create_directory_entry(
                connection_id=a.connection_id,
                extension=a.extension,
                name=a.name,
                destination=a.destination,
                department=a.department,
                ring_timeout=a.timeout,
            )
        ),
    )
    add.add_argument("connection_id")
    add.add_argument("extension", help="four digits")
    add.add_argument("name")
    add.add_argument("destination", help="E.164 number or SIP URI")
    add.add_argument("--department", default="General")
    add.add_argument("--timeout", type=int, default=25, help="ring timeout, seconds")
    update = action(actions, "update", "change an extension", _update_extension)
    update.add_argument("entry_id")
    update.add_argument("--extension")
    update.add_argument("--name")
    update.add_argument("--destination")
    update.add_argument("--department")
    update.add_argument("--timeout", type=int, help="ring timeout, seconds")
    update.add_argument("--enabled", type=on_off, metavar="on|off")
    delete = action(actions, "delete", "delete an extension", _delete_extension)
    delete.add_argument("entry_id")


def register(commands: argparse._SubParsersAction) -> None:
    _register_providers(commands)
    _register_endpoints(commands)
    _register_directory(commands)
