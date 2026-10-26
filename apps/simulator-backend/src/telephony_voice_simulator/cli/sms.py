"""SMS: read the controlled test inbox and its configuration."""

from __future__ import annotations

import argparse

from ..service import sms
from .common import action, emit, group


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "sms", "controlled SMS test inbox")
    listing = action(
        actions,
        "list",
        "stored messages, newest first",
        lambda a: emit(sms.list_messages(limit=a.limit)),
    )
    listing.add_argument("--limit", type=int, default=50)
    action(
        actions, "status", "allowlisted numbers and senders, and whether the inbox is configured",
        lambda _a: emit(sms.status()),
    )
