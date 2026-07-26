"""Command-line control surface; no web UI required."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from aiohttp import web

from ..corpus.release_policy import ReleasePolicyError, validate_public_pstn_assets
from ..domains.ivr import SCENARIOS as IVR_SCENARIOS
from ..domains.ivr import get_scenario as get_ivr_scenario
from ..domains.ivr import simulate as simulate_ivr_model
from ..domains.pbx import EXTENSIONS as PBX_EXTENSIONS
from ..domains.pbx import SCENARIOS as PBX_SCENARIOS
from ..domains.pbx import ExtensionState
from ..domains.pbx import get_scenario as get_pbx_scenario
from ..domains.pbx import simulate as simulate_pbx_model
from ..pstn.config import (
    is_absolute_https_url,
    is_loopback_host,
    is_loopback_http_url,
    is_truthy,
)
from ..paths import ASSETS_DIR, CORPUS_MANIFEST, SCENARIOS_DIR
from .api import build_app
from .service import SimulatorService


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2))


def _model_overrides(values: list[str]) -> dict[str, ExtensionState]:
    known = {extension.id for extension in PBX_EXTENSIONS}
    allowed = {"available", "busy", "offline", "after_hours"}
    overrides: dict[str, ExtensionState] = {}
    for value in values:
        extension_id, separator, state = value.partition("=")
        if not separator or extension_id not in known or state not in allowed:
            expected = "EXTENSION=available|busy|offline|after_hours"
            raise SystemExit(f"Invalid PBX override '{value}'. Expected {expected}.")
        overrides[extension_id] = state  # type: ignore[assignment]
    return overrides


def _simulate_models(
    *,
    scenario_id: str | None,
    kind: str,
    overrides: dict[str, ExtensionState],
) -> dict[str, Any] | list[dict[str, Any]]:
    if kind == "pbx":
        selected = get_pbx_scenario(scenario_id) if scenario_id else None
        if scenario_id and selected is None:
            known = ", ".join(scenario.name for scenario in PBX_SCENARIOS)
            raise SystemExit(f"Unknown PBX scenario '{scenario_id}'. Known: {known}")
        targets = (selected,) if selected else PBX_SCENARIOS
        results = []
        for scenario in targets:
            assert scenario is not None
            steps, outcome = simulate_pbx_model(scenario, overrides)
            results.append(
                {
                    "scenario_id": scenario.name,
                    "title": scenario.title,
                    "caller": scenario.caller,
                    "caller_number": scenario.caller_number,
                    "dialed_number": scenario.dialed_number,
                    "intent": scenario.intent,
                    "outcome": outcome,
                    "expected_summary": scenario.expected_summary,
                    "overrides": overrides,
                    "timeline": [step.as_dict(index) for index, step in enumerate(steps)],
                }
            )
    else:
        selected = get_ivr_scenario(scenario_id) if scenario_id else None
        if scenario_id and selected is None:
            known = ", ".join(scenario.name for scenario in IVR_SCENARIOS)
            raise SystemExit(f"Unknown IVR scenario '{scenario_id}'. Known: {known}")
        targets = (selected,) if selected else IVR_SCENARIOS
        results = []
        for scenario in targets:
            assert scenario is not None
            steps, outcome = simulate_ivr_model(scenario)
            connected_to = next(
                (
                    step.metadata.get("connected_to")
                    for step in reversed(steps)
                    if step.metadata.get("connected_to")
                ),
                None,
            )
            results.append(
                {
                    "scenario_id": scenario.name,
                    "title": scenario.title,
                    "caller_number": scenario.caller_number,
                    "public_number": scenario.public_number,
                    "outcome": outcome,
                    "connected_to": connected_to,
                    "attempts": sum(step.kind == "gather" for step in steps),
                    "timeline": [step.as_dict(index) for index, step in enumerate(steps)],
                }
            )
    return results[0] if scenario_id else results


def _validate_pstn_serve_configuration(host: str, *, include_amd: bool = False) -> None:
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    allow_unsigned_local = is_truthy(os.environ.get("PSTN_ALLOW_UNSIGNED_WEBHOOKS"))
    local_override = (
        allow_unsigned_local
        and is_loopback_host(host)
        and is_loopback_http_url(public_base_url)
    )
    if not is_absolute_https_url(public_base_url) and not local_override:
        raise SystemExit(
            "PUBLIC_BASE_URL must be an absolute HTTPS URL. For an isolated "
            "loopback-only HTTP test server, explicitly set "
            "PSTN_ALLOW_UNSIGNED_WEBHOOKS=true and use a loopback HTTP URL."
        )
    if not os.environ.get("TWILIO_AUTH_TOKEN") and not local_override:
        raise SystemExit(
            "Set TWILIO_AUTH_TOKEN to validate webhooks. Unsigned webhooks are "
            "allowed only with PSTN_ALLOW_UNSIGNED_WEBHOOKS=true on a loopback "
            "bind and loopback HTTP PUBLIC_BASE_URL."
        )
    if include_amd and not local_override:
        if not is_truthy(os.environ.get("PSTN_ACKNOWLEDGE_PUBLIC_AUDIO")):
            raise SystemExit(
                "Public AMD mode exposes compiled scenario audio at /assets. Set "
                "PSTN_ACKNOWLEDGE_PUBLIC_AUDIO=true only after every referenced "
                "source asset has explicit public-release approval."
            )
        try:
            deployment_allowlist = os.environ.get(
                "PSTN_AUDIO_DEPLOYMENT_ALLOWLIST",
                os.environ.get("SIMULATOR_PUBLIC_AUDIO_ALLOWLIST", ""),
            ).strip()
            validate_public_pstn_assets(
                scenarios_dir=SCENARIOS_DIR,
                assets_dir=ASSETS_DIR,
                allowlist_path=(
                    Path(deployment_allowlist) if deployment_allowlist else None
                ),
                manifest_path=CORPUS_MANIFEST,
            )
        except ReleasePolicyError as exc:
            raise SystemExit(f"Public AMD audio policy rejected startup: {exc}") from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="telephony-voice-sim",
        description="Telephony and voice scenario simulator control plane",
    )
    commands = root.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the local simulator API")
    serve.add_argument("--host", default=os.environ.get("SIMULATOR_BIND_HOST", "127.0.0.1"))
    serve.add_argument(
        "--port", type=int, default=int(os.environ.get("SIMULATOR_BIND_PORT", "8978"))
    )
    runtime_mode = serve.add_mutually_exclusive_group()
    runtime_mode.add_argument(
        "--with-pstn",
        action="store_true",
        help="load AMD and IVR Twilio webhooks; requires the private AMD corpus",
    )
    runtime_mode.add_argument(
        "--with-ivr",
        action="store_true",
        help="load only the live Twilio IVR; no AMD audio corpus required",
    )

    commands.add_parser("providers", help="list configured provider connections")
    add_provider = commands.add_parser("add-provider", help="create a provider connection")
    add_provider.add_argument("provider", choices=("mock", "twilio", "telnyx"))
    add_provider.add_argument("name")
    add_provider.add_argument(
        "--status",
        choices=("ready", "needs_setup", "disabled"),
        default="ready",
    )
    add_provider.add_argument("--description", default="")

    commands.add_parser("scenarios", help="list simulator scenarios")
    commands.add_parser("endpoints", help="list configured endpoints")
    add_endpoint = commands.add_parser("add-endpoint", help="create an endpoint")
    add_endpoint.add_argument("connection_id")
    add_endpoint.add_argument("name")
    add_endpoint.add_argument("address")
    add_endpoint.add_argument(
        "--kind", choices=("phone_number", "sip_uri", "extension"), default="phone_number"
    )
    add_endpoint.add_argument("--scenario")
    add_endpoint.add_argument("--routing", choices=("fixed", "queued"), default="fixed")

    commands.add_parser("directory", help="list extension directory routes")
    add_extension = commands.add_parser("add-extension", help="create an extension route")
    add_extension.add_argument("connection_id")
    add_extension.add_argument("extension")
    add_extension.add_argument("name")
    add_extension.add_argument("destination")
    add_extension.add_argument("--department", default="General")
    add_extension.add_argument("--timeout", type=int, default=25)
    delete_extension = commands.add_parser(
        "delete-extension", help="delete an extension route"
    )
    delete_extension.add_argument("entry_id")

    run = commands.add_parser("run", help="run or queue a scenario")
    run.add_argument("endpoint_id")
    run.add_argument("scenario", nargs="?")
    ivr = commands.add_parser("simulate-ivr", help="run a free-form IVR directory call")
    ivr.add_argument("endpoint_id")
    ivr.add_argument("caller_number")
    ivr.add_argument("extension")
    ivr.add_argument(
        "--disposition",
        choices=("connected", "busy", "no_answer", "failed", "abandoned"),
        default="connected",
    )
    model = commands.add_parser(
        "simulate",
        aliases=("sim",),
        help="run built-in IVR or PBX models without a database, endpoint, or credentials",
    )
    model.add_argument("scenario", nargs="?", help="legacy or unified scenario ID")
    model.add_argument("--kind", choices=("ivr", "pbx"), default="ivr")
    model.add_argument(
        "--pbx",
        action="store_true",
        help="compatibility shortcut for --kind pbx",
    )
    model.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="EXTENSION=STATE",
        help="override a PBX extension state for this run; may be repeated",
    )
    commands.add_parser("runs", help="list recent runs")
    commands.add_parser("calls", help="list incoming PSTN calls and recordings")
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command in {"simulate", "sim"}:
        overrides = _model_overrides(args.set)
        _print(
            _simulate_models(
                scenario_id=args.scenario,
                kind="pbx" if args.pbx else args.kind,
                overrides=overrides,
            )
        )
        return 0
    service = SimulatorService()
    if args.command == "serve":
        if args.with_pstn or args.with_ivr:
            _validate_pstn_serve_configuration(
                args.host,
                include_amd=args.with_pstn,
            )
        web.run_app(
            build_app(
                service,
                include_twilio=args.with_pstn,
                include_ivr=args.with_ivr,
            ),
            host=args.host,
            port=args.port,
        )
        return 0
    if args.command == "providers":
        _print(service.list_connections())
    elif args.command == "add-provider":
        _print(
            service.create_connection(
                args.provider,
                args.name,
                status=args.status,
                description=args.description,
            )
        )
    elif args.command == "scenarios":
        _print(service.list_scenarios())
    elif args.command == "endpoints":
        _print(service.list_endpoints())
    elif args.command == "add-endpoint":
        _print(
            service.create_endpoint(
                connection_id=args.connection_id,
                name=args.name,
                kind=args.kind,
                address=args.address,
                routing_mode=args.routing,
                default_scenario=args.scenario,
            )
        )
    elif args.command == "directory":
        _print(service.list_directory_entries())
    elif args.command == "add-extension":
        _print(
            service.create_directory_entry(
                connection_id=args.connection_id,
                extension=args.extension,
                name=args.name,
                destination=args.destination,
                department=args.department,
                ring_timeout=args.timeout,
            )
        )
    elif args.command == "delete-extension":
        service.delete_directory_entry(args.entry_id)
        _print({"deleted": args.entry_id})
    elif args.command == "run":
        _print(asyncio.run(service.create_run(args.endpoint_id, args.scenario)))
    elif args.command == "simulate-ivr":
        _print(
            service.create_ivr_simulation(
                endpoint_id=args.endpoint_id,
                caller_number=args.caller_number,
                extension=args.extension,
                disposition=args.disposition,
            )
        )
    elif args.command == "runs":
        _print(service.list_runs())
    elif args.command == "calls":
        _print(service.list_calls())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
