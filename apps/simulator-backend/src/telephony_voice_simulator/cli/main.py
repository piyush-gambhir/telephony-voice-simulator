"""telephony-voice-sim: every simulator capability from the command line."""

from __future__ import annotations

import argparse
import sys

from ..service import ConflictError, NotFoundError, ValidationError
from . import calls, numbers, pbx, resources, runs, scenarios, serve

GROUPS = (serve, resources, scenarios, runs, calls, numbers, pbx)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="telephony-voice-sim",
        description="Telephony and voice scenario simulator",
    )
    commands = root.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for module in GROUPS:
        module.register(commands)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ValidationError, NotFoundError, ConflictError) as exc:
        print(f"telephony-voice-sim: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
