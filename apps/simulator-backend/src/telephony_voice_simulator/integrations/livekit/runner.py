"""Scenario runner: sink → dispatch → callee bot → assertions → report.

Usage:
    telephony-voice-sim agents room --scenario stock_voicemail_beep_1000   # catalog name or yaml path
    telephony-voice-sim agents room --all                                  # every scenario in the folder
    telephony-voice-sim agents room --all --filter dtmf

Environment (see .env.example): LIVEKIT_URL / LIVEKIT_API_KEY /
LIVEKIT_API_SECRET, AGENT_NAME, METADATA_TEMPLATE, WEBHOOK_BIND_HOST/PORT.

Each run writes ``results/<timestamp>/<scenario>/`` containing the machine
timeline, every recorded agent track (+ clock sidecars), all captured
webhooks, and ``report.json`` with per-check pass/fail.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ...domains.amd.machine import SimulatedMachine
from ...domains.amd.validation import validate_scenario
from ...grading.checks import run_checks, summarize_checks
from ...paths import ASSETS_DIR, RESULTS_DIR, TEMPLATES_DIR
from ...service.errors import ValidationError
from .callee import CalleeBot
from .dispatch import build_metadata, create_dispatch, new_call_id
from .webhook_sink import WebhookSink


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ValidationError(f"Missing required env var {name} (see .env.example)")
    return value


def load_scenario(path: Path) -> dict[str, Any]:
    return validate_scenario(yaml.safe_load(path.read_text()), source=str(path))


class _NullAudio:
    async def play(self, samples, sample_rate) -> None:  # replaced by the bot
        raise RuntimeError("machine ran before the bot bound its audio track")


async def run_scenario(
    scenario: dict[str, Any],
    *,
    sink: WebhookSink,
    results_root: Path,
) -> dict[str, Any]:
    validate_scenario(scenario)
    name = scenario["name"]
    call_id, room_name = new_call_id(name)
    results_dir = results_root / name
    results_dir.mkdir(parents=True, exist_ok=True)

    webhook_url = os.environ.get("WEBHOOK_PUBLIC_URL", sink.url)
    metadata = build_metadata(
        Path(_env("METADATA_TEMPLATE", str(TEMPLATES_DIR / "outbound.metadata.json"))),
        call_id=call_id,
        room_name=room_name,
        webhook_url=webhook_url,
        config_overrides=scenario.get("config_overrides"),
    )
    # The agent derives the callee identity as ``phone-<phone_number>``
    # (call_handlers/outbound.py) — the bot must join with exactly that.
    phone = str(metadata.get("phone_number", "+15550100001"))
    identity = f"phone-{phone}"
    (results_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    machine = SimulatedMachine(
        scenario["machine"], assets_dir=ASSETS_DIR, audio_out=_NullAudio()
    )
    bot = CalleeBot(
        url=_env("LIVEKIT_URL"),
        api_key=_env("LIVEKIT_API_KEY"),
        api_secret=_env("LIVEKIT_API_SECRET"),
        room_name=room_name,
        identity=identity,
        machine=machine,
        results_dir=results_dir,
    )

    await create_dispatch(
        url=_env("LIVEKIT_URL"),
        api_key=_env("LIVEKIT_API_KEY"),
        api_secret=_env("LIVEKIT_API_SECRET"),
        agent_name=_env("AGENT_NAME"),
        room_name=room_name,
        metadata=metadata,
    )
    # Small head start so the agent's session/AMD pipeline is up before the
    # "callee answers" (mirrors dial+ring time on a real call).
    await asyncio.sleep(float(scenario.get("answer_delay", 2.0)))

    bot_result = await bot.run(call_timeout=float(scenario.get("call_timeout", 180)))
    call_ended = await sink.wait_for_event(
        "call.ended", call_id, timeout=float(scenario.get("webhook_timeout", 90))
    )
    (results_dir / "webhooks.json").write_text(json.dumps(sink.events_for(call_id), indent=2))

    checks = run_checks(
        scenario["expect"],
        timeline=bot_result.timeline,
        recordings=bot_result.recordings,
        call_ended=call_ended,
    )
    passed = summarize_checks(c.passed for c in checks)
    report = {
        "scenario": name,
        "call_id": call_id,
        "room": room_name,
        "passed": passed,
        "checks": [c.as_dict() for c in checks],
    }
    (results_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


async def run_scenarios(
    paths: list[Path],
    *,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run each scenario file in turn behind one webhook sink; returns the summary."""
    say = log or (lambda _line: None)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results_root = RESULTS_DIR / stamp
    results_root.mkdir(parents=True, exist_ok=True)

    sink = WebhookSink(
        host=os.environ.get("WEBHOOK_BIND_HOST", "127.0.0.1"),
        port=int(os.environ.get("WEBHOOK_BIND_PORT", "8977")),
    )
    await sink.start()
    reports = []
    try:
        for path in paths:
            scenario = load_scenario(path)
            if scenario.get("pstn_only"):
                say(f"-> {scenario['name']} (skipped: pstn_only)")
                continue
            say(f"-> {scenario['name']}")
            report = await run_scenario(scenario, sink=sink, results_root=results_root)
            status = "PASS" if report["passed"] else "INDETERMINATE" if report["passed"] is None else "FAIL"
            say(f"  {status}  ({len(report['checks'])} checks)")
            for check in report["checks"]:
                flag = "ok" if check["passed"] else "?" if check["passed"] is None else "FAIL"
                say(f"    {flag} {check['check']}: {check['detail']}")
            reports.append(report)
    finally:
        await sink.stop()

    summary = {
        "total": len(reports),
        "passed": sum(1 for r in reports if r["passed"]),
        "results_dir": str(results_root),
        "reports": reports,
    }
    (results_root / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
