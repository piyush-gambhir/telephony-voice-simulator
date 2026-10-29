"""Customer: the synthetic customer's personas and queue, its ElevenLabs agents, and grading.

``grade`` and ``check-persona`` exit 2 when the result fails, so scripts and coding agents
can gate on the exit code and read the JSON on stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..service import ValidationError, customer
from .common import action, emit, group, service

FAILED = 2


def _progress(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _inline(args: argparse.Namespace) -> str | None:
    if args.file:
        try:
            return Path(args.file).read_text()
        except OSError as exc:
            raise ValidationError(f"Cannot read {args.file}: {exc}") from exc
    return args.inline


def _persona_options(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--persona", help="catalog persona id")
    source.add_argument("--file", help="persona YAML or JSON file")
    source.add_argument("--inline", help="persona as YAML or JSON text")


def _queue(args: argparse.Namespace) -> int:
    return emit(customer.queue_persona(
        service().store, args.number, persona_id=args.persona, inline=_inline(args),
        label=args.label,
    ))


def _dial(args: argparse.Namespace) -> int:
    persona_id = args.persona
    inline = _inline(args) if (args.file or args.inline) else None
    return emit(customer.dial(
        service().store, args.number, args.to, persona_id=persona_id, inline=inline,
        label=args.label,
    ))


def _check(args: argparse.Namespace) -> int:
    persona = customer.resolve_persona(args.persona, _inline(args))
    result = customer.check_persona(persona, repeat=args.repeat, progress=_progress)
    emit(result)
    return 0 if result["passed"] == len(result["runs"]) else FAILED


def _grade(args: argparse.Namespace) -> int:
    grade = customer.grade_session(
        service().store, args.call_id, wait=args.wait, progress=_progress
    )
    emit(grade)
    return 0 if grade["passed"] else FAILED


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "customer", "synthetic customer")
    action(
        actions,
        "profiles",
        "persona lines, their default persona, and which environment keys are set",
        lambda _a: emit(customer.profiles()),
    )
    action(actions, "personas", "the persona catalog", lambda _a: emit(customer.list_personas()))
    show = action(
        actions, "persona", "one catalog persona", lambda a: emit(customer.get_persona(a.id))
    )
    show.add_argument("id")
    queue = action(actions, "queue", "queue a persona for the next call on a line", _queue)
    queue.add_argument("number", help="persona line, E.164")
    _persona_options(queue)
    queue.add_argument("--label", help="free text kept with the call, e.g. a branch or run id")
    dial = action(
        actions, "dial", "have a persona line call a dealership's inbound number", _dial
    )
    dial.add_argument("number", help="persona line placing the call, E.164")
    dial.add_argument("--to", required=True, help="inbound number of the agent under test, E.164")
    source = dial.add_mutually_exclusive_group()
    source.add_argument("--persona", help="catalog persona id; omit to use the line default")
    source.add_argument("--file", help="persona YAML or JSON file")
    source.add_argument("--inline", help="persona as YAML or JSON text")
    dial.add_argument("--label", help="free text kept with the call, e.g. a branch or run id")
    queued = action(
        actions,
        "queued",
        "personas waiting for a call, oldest first",
        lambda a: emit(customer.list_queue(service().store, a.number)),
    )
    queued.add_argument("--number")
    unqueue = action(
        actions,
        "unqueue",
        "remove a queued persona",
        lambda a: emit(customer.cancel_queued(service().store, a.queue_id)),
    )
    unqueue.add_argument("queue_id")
    sessions = action(
        actions,
        "sessions",
        "calls that carry a customer dialogue, newest first",
        lambda a: emit(customer.list_sessions(service().store, limit=a.limit)),
    )
    sessions.add_argument("--limit", type=int, default=20)
    action(
        actions,
        "sync-agent",
        "configure each persona agent (creating variant agents for background sound and "
        "turn-taking) and move its pinned telephony-voice-simulator branch; turns on "
        "ElevenLabs versioning, which cannot be turned off",
        lambda _a: emit(customer.sync_agent()),
    )
    refresh = action(
        actions,
        "refresh",
        "fetch a session's transcript from ElevenLabs",
        lambda a: emit(customer.refresh_session(service().store, a.call_id)),
    )
    refresh.add_argument("call_id")
    grade = action(
        actions,
        "grade",
        "grade a finished call against its persona (exit 2 when it fails)",
        _grade,
    )
    grade.add_argument("call_id")
    grade.add_argument("--wait", action="store_true", help="wait for the transcript first")
    check = action(
        actions,
        "check-persona",
        "run a persona's ElevenLabs fidelity test (exit 2 when any run fails)",
        _check,
    )
    _persona_options(check)
    check.add_argument("--repeat", type=int, default=1)
