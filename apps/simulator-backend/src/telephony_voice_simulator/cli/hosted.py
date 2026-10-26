"""The Twilio-hosted callee: deploy, queue, smoke-test, grade, and LiveKit agent calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..service import ValidationError, hosted
from .common import action, emit, group, run


def _log(line: str) -> None:
    print(line, file=sys.stderr)


def _deploy(args: argparse.Namespace) -> int:
    try:
        static_map = json.loads(args.static)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"--static is not valid JSON: {exc}") from exc
    if not isinstance(static_map, dict):
        raise ValidationError("--static must be a JSON object of number to scenario")
    return emit(
        hosted.deploy(
            static_map=static_map,
            default_scenario=args.default_scenario,
            acknowledge_public_audio=args.acknowledge_public_audio,
            audio_deployment_allowlist=args.audio_deployment_allowlist,
            log=_log,
        )
    )


def _smoke(args: argparse.Namespace) -> int:
    result = hosted.smoke_call(
        number=args.number,
        scenario=args.scenario,
        from_number=args.from_number,
        log=_log,
    )
    emit(result)
    return 0 if result["passed"] else 1


def _grade(args: argparse.Namespace) -> int:
    result = hosted.grade_calls(
        call_sid=args.call_sid, number=args.number, last=args.last, log=_log
    )
    emit(result)
    return 0 if result["passed"] else 1


def _agent_call(args: argparse.Namespace) -> int:
    result = run(
        hosted.agent_call(
            scenario=args.scenario,
            queue=not args.no_queue,
            wait=not args.no_wait,
            log=_log,
        )
    )
    emit(result)
    return 0 if args.no_wait or result["passed"] else 1


def register(commands: argparse._SubParsersAction) -> None:
    actions = group(commands, "hosted", "Twilio-hosted callee: needs only Twilio credentials")
    deploy = action(actions, "deploy", "deploy the callee to Twilio Serverless", _deploy)
    deploy.add_argument("--static", default="{}", help="JSON number->scenario static map")
    deploy.add_argument("--default-scenario", default=hosted.DEFAULT_SCENARIO)
    deploy.add_argument(
        "--acknowledge-public-audio",
        action="store_true",
        help="confirm that every deployed compiled WAV will be publicly reachable",
    )
    deploy.add_argument(
        "--audio-deployment-allowlist",
        type=Path,
        help="reviewed version-1 JSON allowlist of source WAV filenames",
    )
    queue = action(
        actions,
        "queue",
        "append a scenario to a hosted number's queue",
        lambda a: emit(hosted.queue_scenario(number=a.number, scenario=a.scenario)),
    )
    queue.add_argument("--number", required=True)
    queue.add_argument("--scenario", required=True)
    smoke = action(
        actions, "smoke", "queue, place a real call to the sim line, and grade it", _smoke
    )
    smoke.add_argument("--number", required=True)
    smoke.add_argument("--scenario", required=True)
    smoke.add_argument("--from-number", help="caller ID; defaults to TWILIO_FROM_NUMBER")
    grade = action(actions, "grade", "grade recorded sim calls", _grade)
    grade.add_argument("--call-sid")
    grade.add_argument("--number")
    grade.add_argument("--last", type=int, default=3, help="inbound legs to grade with --number")
    agent = action(
        actions,
        "agent-call",
        "dispatch a deployed LiveKit agent to call the sim number",
        _agent_call,
    )
    agent.add_argument("--scenario", default=hosted.DEFAULT_SCENARIO)
    agent.add_argument(
        "--no-queue", action="store_true", help="don't queue; the number's static/default answers"
    )
    agent.add_argument("--no-wait", action="store_true", help="dispatch only")
