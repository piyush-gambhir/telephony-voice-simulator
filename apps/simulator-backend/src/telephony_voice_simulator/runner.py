"""Scenario runner: sink → dispatch → callee bot → assertions → report.

Usage:
    python -m telephony_voice_simulator.runner --scenario \
      src/telephony_voice_simulator/scenarios/01_stock_voicemail_beep_1000.yaml
    python -m telephony_voice_simulator.runner --all            # every scenario in the folder
    python -m telephony_voice_simulator.runner --all --filter dtmf

Environment (see .env.example): LIVEKIT_URL / LIVEKIT_API_KEY /
LIVEKIT_API_SECRET, AGENT_NAME, METADATA_TEMPLATE, WEBHOOK_BIND_HOST/PORT.

Each run writes ``results/<timestamp>/<scenario>/`` containing the machine
timeline, every recorded agent track (+ clock sidecars), all captured
webhooks, and ``report.json`` with per-check pass/fail.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .assertions import run_checks
from .bot import CalleeBot
from .dispatch import build_metadata, create_dispatch, new_call_id
from .machine import SimulatedMachine
from .paths import ASSETS_DIR, RESULTS_DIR, SCENARIOS_DIR, TEMPLATES_DIR
from .webhook_sink import WebhookSink


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        print(f"Missing required env var {name} (see .env.example)", file=sys.stderr)
        sys.exit(2)
    return value


def load_scenario(path: Path) -> dict[str, Any]:
    scenario = yaml.safe_load(path.read_text())
    for key in ("name", "machine", "expect"):
        if key not in scenario:
            raise ValueError(f"{path.name}: missing required key {key!r}")
    return scenario


class _NullAudio:
    async def play(self, samples, sample_rate) -> None:  # replaced by the bot
        raise RuntimeError("machine ran before the bot bound its audio track")


async def run_scenario(
    scenario: dict[str, Any],
    *,
    sink: WebhookSink,
    results_root: Path,
) -> dict[str, Any]:
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
    passed = all(c.passed for c in checks)
    report = {
        "scenario": name,
        "call_id": call_id,
        "room": room_name,
        "passed": passed,
        "checks": [c.as_dict() for c in checks],
    }
    (results_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report


async def main() -> int:
    parser = argparse.ArgumentParser(description="Voice-agent scenario simulator")
    parser.add_argument("--scenario", help="path to one scenario yaml")
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--filter", default="", help="substring filter with --all")
    args = parser.parse_args()

    if args.scenario:
        paths = [Path(args.scenario)]
    elif args.all:
        paths = sorted(SCENARIOS_DIR.glob("*.yaml"))
        if args.filter:
            paths = [p for p in paths if args.filter in p.name]
    else:
        parser.error("pass --scenario <file> or --all")
        return 2
    if not paths:
        print("No scenarios matched.", file=sys.stderr)
        return 2

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
                print(f"→ {scenario['name']} (skipped: pstn_only)")
                continue
            print(f"→ {scenario['name']}")
            report = await run_scenario(scenario, sink=sink, results_root=results_root)
            status = "PASS" if report["passed"] else "FAIL"
            print(f"  {status}  ({len(report['checks'])} checks)")
            for check in report["checks"]:
                flag = "✓" if check["passed"] else "✗"
                print(f"    {flag} {check['check']}: {check['detail']}")
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
    print(f"\n{summary['passed']}/{summary['total']} scenarios passed → {results_root}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
