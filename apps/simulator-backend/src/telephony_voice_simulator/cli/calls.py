"""Incoming calls, their recordings and their grades."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .common import action, emit, group, service


def _recording(args: argparse.Namespace) -> int:
    path = service().recording_path(args.recording_id)
    if args.out is None:
        return emit({"recording_id": args.recording_id, "path": str(path)})
    shutil.copyfile(path, args.out)
    return emit({"recording_id": args.recording_id, "saved": str(args.out)})


def _delete(args: argparse.Namespace) -> int:
    return emit(service().delete_call(args.call_id))


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "calls", "calls the simulator answered, with recordings and grades")
    action(actions, "list", "recent calls", lambda _a: emit(service().list_calls()))
    show = action(actions, "show", "one call", lambda a: emit(service().get_call(a.call_id)))
    show.add_argument("call_id")
    delete = action(actions, "delete", "delete a call and its recordings", _delete)
    delete.add_argument("call_id")
    recording = action(actions, "recording", "locate or save a recording", _recording)
    recording.add_argument("recording_id")
    recording.add_argument("--out", type=Path, help="copy the audio to this file")
