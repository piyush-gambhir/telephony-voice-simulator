"""Deploy the simulated machine to Twilio Serverless (Functions + Assets).

Everything the PSTN sim needs ends up hosted BY Twilio — no tunnel, no
laptop, always-on:

  * ``/machine``          protected Function (twilio_hosted/machine.js)
  * ``/programs.json``    private asset: compiled scenario programs
  * ``/<scenario>.wav``   public assets: pre-rendered scenario audio
  * per-call state + scenario queues in Twilio Sync (default service)

Usage:
    python -m telephony_voice_simulator.pstn.deploy_twilio \
      --acknowledge-public-audio \
      --audio-deployment-allowlist /private/deployment_allowlist.json
    python -m telephony_voice_simulator.pstn.deploy_twilio \
      --acknowledge-public-audio \
      --audio-deployment-allowlist /private/deployment_allowlist.json \
      --static '{"+15551234567":"stock_voicemail_beep_1000"}'

Idempotent: reuses the ``telephony-voice-simulator`` service and creates a fresh build +
deployment each run. Prints the environment domain when done.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

from ..corpus.release_policy import load_public_allowlist
from ..paths import ASSETS_DIR, COMPILED_ASSETS_DIR, CORPUS_MANIFEST, SCENARIOS_DIR
from .compile import compile_scenario

API = "https://serverless.twilio.com/v1"
COMPILED_DIR = COMPILED_ASSETS_DIR
MACHINE_JS = Path(__file__).parent / "twilio_hosted" / "machine.js"
SERVICE_NAME = "telephony-voice-simulator"
ENV_SUFFIX = "sim"


def validate_public_audio_deployment(
    *,
    acknowledged: bool,
    allowlist_path: Path | None,
    scenarios_dir: Path | None = None,
    assets_dir: Path | None = None,
    manifest_path: Path | None = None,
) -> frozenset[str]:
    """Fail closed before compiled call audio is uploaded as public assets."""

    if not acknowledged:
        raise ValueError(
            "Twilio-hosted scenario audio is publicly reachable; pass "
            "--acknowledge-public-audio only after a rights/privacy review"
        )
    if allowlist_path is None:
        raise ValueError("--audio-deployment-allowlist is required")

    scenarios_dir = scenarios_dir or SCENARIOS_DIR
    assets_dir = assets_dir or ASSETS_DIR
    manifest_path = manifest_path or CORPUS_MANIFEST
    allowed = load_public_allowlist(allowlist_path)
    manifest = json.loads(manifest_path.read_text())
    referenced: set[str] = set()
    for scenario_path in sorted(scenarios_dir.glob("*.yaml")):
        scenario = yaml.safe_load(scenario_path.read_text())
        for steps in (scenario.get("machine") or {}).values():
            if not isinstance(steps, list):
                continue
            for step in steps:
                if isinstance(step, dict) and step.get("play"):
                    referenced.add(str(step["play"]))

    errors: list[str] = []
    for asset in sorted(referenced):
        row = manifest.get(asset)
        source = assets_dir / asset
        if asset not in allowed:
            errors.append(f"{asset}: absent from deployment allowlist")
            continue
        if not isinstance(row, dict):
            errors.append(f"{asset}: missing provenance manifest row")
            continue
        if not row.get("source_url") or not row.get("license_note"):
            errors.append(f"{asset}: incomplete provenance")
        expected = row.get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            errors.append(f"{asset}: missing SHA-256 provenance")
        elif not source.is_file():
            errors.append(f"{asset}: source audio is missing")
        elif hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            errors.append(f"{asset}: source audio checksum mismatch")
    if errors:
        raise ValueError(
            "public audio deployment is not approved:\n- " + "\n- ".join(errors)
        )
    return frozenset(referenced)


def _client() -> httpx.Client:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        sys.exit("Set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN")
    return httpx.Client(auth=(sid, token), timeout=60)


def compile_programs(static_map: dict[str, str], default_scenario: str) -> dict:
    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    programs: dict = {"scenarios": {}, "static_map": static_map,
                      "default_scenario": default_scenario}
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        compiled = compile_scenario(raw, assets_dir=ASSETS_DIR, out_dir=COMPILED_DIR)
        programs["scenarios"][compiled.name] = {
            "on_dtmf": compiled.on_dtmf,
            "gather_timeout": compiled.gather_timeout,
            "sequences": {
                seq: [dataclasses.asdict(s) for s in segments]
                for seq, segments in compiled.sequences.items()
            },
        }
    return programs


def _get_or_create_service(client: httpx.Client) -> str:
    resp = client.get(f"{API}/Services")
    resp.raise_for_status()
    for service in resp.json().get("services", []):
        if service["unique_name"] == SERVICE_NAME:
            return service["sid"]
    resp = client.post(
        f"{API}/Services",
        data={
            "UniqueName": SERVICE_NAME,
            "FriendlyName": "Telephony Voice Simulator",
            "IncludeCredentials": "true",
        },
    )
    resp.raise_for_status()
    return resp.json()["sid"]


def _get_or_create(client: httpx.Client, url: str, name: str) -> str:
    resp = client.get(url)
    resp.raise_for_status()
    for item in list(resp.json().values())[0]:
        if isinstance(item, dict) and item.get("friendly_name") == name:
            return item["sid"]
    resp = client.post(url, data={"FriendlyName": name})
    resp.raise_for_status()
    return resp.json()["sid"]


def _upload_version(
    client: httpx.Client,
    service_sid: str,
    kind: str,  # "Functions" | "Assets"
    item_sid: str,
    path: str,
    content: bytes,
    content_type: str,
    visibility: str,
) -> str:
    resp = client.post(
        f"https://serverless-upload.twilio.com/v1/Services/{service_sid}/{kind}/{item_sid}/Versions",
        data={"Path": path, "Visibility": visibility},
        files={"Content": (path.lstrip("/"), content, content_type)},
    )
    resp.raise_for_status()
    return resp.json()["sid"]


def _wait_for_build(
    client: httpx.Client,
    service_sid: str,
    build_sid: str,
    *,
    attempts: int = 60,
    delay_s: float = 3,
) -> None:
    last_status = "unknown"
    for _ in range(attempts):
        if delay_s:
            time.sleep(delay_s)
        response = client.get(
            f"{API}/Services/{service_sid}/Builds/{build_sid}/Status"
        )
        response.raise_for_status()
        last_status = str(response.json().get("status") or "unknown")
        print(".", end="", flush=True)
        if last_status == "completed":
            return
        if last_status == "failed":
            raise RuntimeError(f"Twilio build failed: {build_sid}")
    raise RuntimeError(
        f"Twilio build timed out after {attempts} polls: "
        f"{build_sid} (last status: {last_status})"
    )


def deploy(
    static_map: dict[str, str],
    default_scenario: str,
    *,
    acknowledge_public_audio: bool,
    audio_deployment_allowlist: Path | None,
) -> str:
    approved = validate_public_audio_deployment(
        acknowledged=acknowledge_public_audio,
        allowlist_path=audio_deployment_allowlist,
    )
    print(f"public audio deployment approved for {len(approved)} source asset(s)")
    client = _client()
    programs = compile_programs(static_map, default_scenario)
    service_sid = _get_or_create_service(client)
    print(f"service: {service_sid}")

    # Function
    function_sid = _get_or_create(client, f"{API}/Services/{service_sid}/Functions", "machine")
    function_version = _upload_version(
        client, service_sid, "Functions", function_sid, "/machine",
        MACHINE_JS.read_bytes(), "application/javascript", "protected",
    )
    print(f"function version: {function_version}")

    # Assets: programs.json (private) + every compiled wav referenced (public)
    asset_versions: list[str] = []
    programs_asset = _get_or_create(client, f"{API}/Services/{service_sid}/Assets", "programs")
    asset_versions.append(
        _upload_version(
            client, service_sid, "Assets", programs_asset, "/programs.json",
            json.dumps(programs).encode(), "application/json", "private",
        )
    )
    referenced = sorted(
        {
            segment["audio_file"]
            for scenario in programs["scenarios"].values()
            for segments in scenario["sequences"].values()
            for segment in segments
            if segment["audio_file"]
        }
    )
    for filename in referenced:
        asset_sid = _get_or_create(
            client, f"{API}/Services/{service_sid}/Assets", filename
        )
        asset_versions.append(
            _upload_version(
                client, service_sid, "Assets", asset_sid, f"/{filename}",
                (COMPILED_DIR / filename).read_bytes(), "audio/wav", "public",
            )
        )
        print(f"asset: {filename}")

    # Build (dict with list values → repeated form keys; httpx treats a
    # list-of-tuples ``data`` as raw content, not form fields)
    data = {
        "FunctionVersions": function_version,
        "AssetVersions": asset_versions,
        "Dependencies": json.dumps([
            {"name": "twilio", "version": "^4.23.0"},
            {"name": "@twilio/runtime-handler", "version": "2.0.3"},
        ]),
        "Runtime": "node22",
    }
    resp = client.post(f"{API}/Services/{service_sid}/Builds", data=data)
    resp.raise_for_status()
    build_sid = resp.json()["sid"]
    print(f"build: {build_sid} ", end="", flush=True)
    _wait_for_build(client, service_sid, build_sid)
    print(" completed")

    # Environment + deployment
    resp = client.get(f"{API}/Services/{service_sid}/Environments")
    resp.raise_for_status()
    env = next(
        (e for e in resp.json()["environments"] if e["unique_name"] == ENV_SUFFIX), None
    )
    if env is None:
        resp = client.post(
            f"{API}/Services/{service_sid}/Environments",
            data={"UniqueName": ENV_SUFFIX, "DomainSuffix": ENV_SUFFIX},
        )
        resp.raise_for_status()
        env = resp.json()
    bridge_number = os.environ.get("BRIDGE_NUMBER", "")
    if bridge_number:
        _set_env_variable(client, service_sid, env["sid"], "BRIDGE_NUMBER", bridge_number)
        print(f"bridge number set (dial: bridge → {bridge_number})")

    resp = client.post(
        f"{API}/Services/{service_sid}/Environments/{env['sid']}/Deployments",
        data={"BuildSid": build_sid},
    )
    resp.raise_for_status()
    domain = env["domain_name"]
    print(f"deployed → https://{domain}/machine")
    return domain


def _set_env_variable(
    client: httpx.Client, service_sid: str, env_sid: str, key: str, value: str
) -> None:
    url = f"{API}/Services/{service_sid}/Environments/{env_sid}/Variables"
    resp = client.get(url)
    resp.raise_for_status()
    existing = next((v for v in resp.json()["variables"] if v["key"] == key), None)
    if existing:
        client.post(f"{url}/{existing['sid']}", data={"Value": value}).raise_for_status()
    else:
        client.post(url, data={"Key": key, "Value": value}).raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--static", default="{}", help="JSON number->scenario static map")
    parser.add_argument("--default-scenario", default="stock_voicemail_beep_1000")
    parser.add_argument(
        "--acknowledge-public-audio",
        action="store_true",
        help="confirm that every deployed compiled WAV will be publicly reachable",
    )
    parser.add_argument(
        "--audio-deployment-allowlist",
        type=Path,
        help="reviewed version-1 JSON allowlist of source WAV filenames",
    )
    args = parser.parse_args()

    try:
        deploy(
            json.loads(args.static),
            args.default_scenario,
            acknowledge_public_audio=args.acknowledge_public_audio,
            audio_deployment_allowlist=args.audio_deployment_allowlist,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"deployment refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
