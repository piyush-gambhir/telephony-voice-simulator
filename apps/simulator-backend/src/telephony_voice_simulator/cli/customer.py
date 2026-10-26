"""Customer: the synthetic customer's personas and recorded sessions."""

from __future__ import annotations

import argparse

from ..service import customer
from .common import action, emit, group, service


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "customer", "synthetic customer")
    action(
        actions,
        "profiles",
        "personas by number and which required environment keys are set",
        lambda _a: emit(customer.profiles()),
    )
    sessions = action(
        actions,
        "sessions",
        "calls that carry a customer dialogue, newest first",
        lambda a: emit(customer.list_sessions(service().store, limit=a.limit)),
    )
    sessions.add_argument("--limit", type=int, default=20)
