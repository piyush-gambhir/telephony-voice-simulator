"""Helpers shared by every command group."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable, Coroutine
from functools import cache
from typing import Any

from ..service import SimulatorService

Handler = Callable[[argparse.Namespace], int]


@cache
def service() -> SimulatorService:
    """Built on first use, so commands that need no database never create one."""
    return SimulatorService()


def emit(value: Any) -> int:
    print(json.dumps(value, indent=2, default=str))
    return 0


def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coroutine)


def pairs(values: list[str], label: str) -> dict[str, str]:
    """Parse repeated KEY=VALUE options."""
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key:
            raise SystemExit(f"Invalid {label} '{value}'. Expected KEY=VALUE.")
        parsed[key] = item
    return parsed


def group(
    commands: argparse._SubParsersAction, name: str, help_text: str
) -> argparse._SubParsersAction:
    """Add a command group whose actions are required subcommands."""
    parser = commands.add_parser(name, help=help_text, description=help_text)
    return parser.add_subparsers(dest="action", required=True, metavar="ACTION")


def action(
    actions: argparse._SubParsersAction, name: str, help_text: str, handler: Handler
) -> argparse.ArgumentParser:
    parser = actions.add_parser(name, help=help_text, description=help_text)
    parser.set_defaults(handler=handler)
    return parser


def on_off(value: str) -> bool:
    if value not in {"on", "off"}:
        raise argparse.ArgumentTypeError("expected on or off")
    return value == "on"
