"""Transcript analysis."""

from __future__ import annotations

import argparse

from ..service import analysis
from .common import action, emit, group


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "analyze", "analyze exported call transcripts")
    silence = action(
        actions,
        "silence",
        "classify silence_timeout callee transcripts (JSONL of call_uid, user_text)",
        lambda a: emit(analysis.classify_silence(a.file)),
    )
    silence.add_argument("file", help="JSONL file of {call_uid, user_text} rows")
