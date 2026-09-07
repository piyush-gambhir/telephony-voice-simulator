"""Control + grade the Twilio-hosted sim from your machine (creds only, no server).

    python -m telephony_voice_simulator.pstn.grade_hosted queue --number +1425... --scenario dtmf_gate_honored
    python -m telephony_voice_simulator.pstn.grade_hosted smoke --number +1425... --scenario stock_voicemail_beep_1000
    python -m telephony_voice_simulator.pstn.grade_hosted grade --call-sid CAxxxx
    python -m telephony_voice_simulator.pstn.grade_hosted grade --number +1425... --last 5

``smoke`` places a REAL outbound PSTN call from your Twilio number to the
sim line, with a caller script that imitates the agent (waits through the
greeting+beep, then speaks a test message; for gate scenarios, sends DTMF) —
a full carrier-loop validation of the machine without involving the voice
agent. ``grade`` pulls the Sync call doc (scenario, digits), downloads the
recordings, and runs the same analyzer as server mode.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import yaml

from ..paths import ASSETS_DIR, RESULTS_DIR, SCENARIOS_DIR
from .analyze import analyze_call

PSTN_RESULTS_DIR = RESULTS_DIR / "pstn"
API = "https://api.twilio.com/2010-04-01"
SYNC = "https://sync.twilio.com/v1/Services/default"


def _client() -> tuple[httpx.Client, str]:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        sys.exit("Set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN")
    return httpx.Client(auth=(sid, token), timeout=60), sid


def _load_scenario(name: str) -> dict:
    for path in SCENARIOS_DIR.glob("*.yaml"):
        raw = yaml.safe_load(path.read_text())
        if raw.get("name") == name:
            return raw
    sys.exit(f"unknown scenario {name}")


def _apply_expected_voicemail_override(
    scenario: dict,
    expected_voicemail_message: str | None = None,
    allowed_identity_name: str | None = None,
) -> dict:
    """Let live-agent PSTN runs grade against a configured message.

    YAML scenarios carry a portable default fixture. Integrations can override
    it with the message configured for the agent under test.
    """
    expected = (
        expected_voicemail_message
        or os.environ.get("UAT_EXPECTED_VOICEMAIL_MESSAGE")
        or os.environ.get("EXPECTED_VOICEMAIL_MESSAGE")
    )
    if not expected:
        return scenario

    message_content = (scenario.get("expect") or {}).get("message_content")
    if not isinstance(message_content, dict):
        return scenario

    patched = dict(scenario)
    patched_expect = dict(scenario.get("expect") or {})
    patched_message = dict(message_content)
    patched_message["expected"] = expected
    if allowed_identity_name:
        patched_message["allowed_identity_name"] = allowed_identity_name
    patched_expect["message_content"] = patched_message
    patched["expect"] = patched_expect
    return patched


def cmd_queue(args) -> int:
    client, _ = _client()
    _load_scenario(args.scenario)
    name = f"queue-{args.number}"
    resp = client.get(f"{SYNC}/Documents/{name}")
    queue = resp.json().get("data", {}).get("queue", []) if resp.status_code == 200 else []
    queue.append(args.scenario)
    payload = {"Data": json.dumps({"queue": queue}), "Ttl": "86400"}
    if resp.status_code == 200:
        resp = client.post(f"{SYNC}/Documents/{name}", data=payload)
    else:
        resp = client.post(f"{SYNC}/Documents", data={"UniqueName": name, **payload})
    resp.raise_for_status()
    print(f"queued {args.scenario} on {args.number} (queue={queue})")
    return 0


def _caller_twiml(scenario: dict) -> str:
    """A caller leg that imitates the agent for this scenario."""
    machine = scenario["machine"]
    on_dtmf = machine.get("on_dtmf")
    if on_dtmf is not None:
        digit = "5"
        if isinstance(on_dtmf, dict) and on_dtmf.get("digits"):
            digit = str(on_dtmf["digits"][0])
        # Wait into the prompt, press, wait for the connect, say hello, linger.
        return (
            "<Response><Pause length='8'/>"
            f"<Play digits='{digit}'/>"
            "<Pause length='4'/><Say>Hello, can you hear me? This is a test caller.</Say>"
            "<Pause length='15'/><Hangup/></Response>"
        )
    # Voicemail-ish: wait until just past the machine's audio (greeting+beep),
    # then leave the "message". Use the COMPILED audio duration — an estimate
    # overshoots the beep and grades as late-message even though the machine
    # behaved perfectly.
    try:
        import tempfile

        from .compile import compile_scenario

        with tempfile.TemporaryDirectory() as tmp:
            compiled = compile_scenario(
                scenario,
                assets_dir=ASSETS_DIR,
                out_dir=Path(tmp),
            )
        total_audio = compiled.sequences["main"][0].audio_duration
    except Exception:
        total_audio = sum(
            float(s.get("wait", 0))
            + float((s.get("tone") or {}).get("duration", 0.5) if "tone" in s else 0)
            + (12.0 if "play" in s else 0)
            for s in machine.get("main", [])
        )
    pause = min(30, math.ceil(total_audio) + 1)
    # Speak the scenario's expected message so the content check grades the
    # smoke exactly like an agent call; fall back to a generic line.
    expected = (scenario.get("expect", {}).get("message_content") or {}).get("expected")
    message = escape(
        expected
        or "Hello, this is a simulated agent message for the A M D simulator "
        "smoke test. Please call us back at your convenience. Goodbye."
    )
    return (
        f"<Response><Pause length='{pause}'/><Say>{message}</Say>"
        "<Pause length='2'/><Hangup/></Response>"
    )


def cmd_smoke(args) -> int:
    client, account = _client()
    scenario = _load_scenario(args.scenario)
    cmd_queue(args)
    from_number = args.from_number or os.environ.get("TWILIO_FROM_NUMBER", "")
    if not from_number:
        sys.exit("--from-number or TWILIO_FROM_NUMBER required")
    resp = client.post(
        f"{API}/Accounts/{account}/Calls.json",
        data={"To": args.number, "From": from_number, "Twiml": _caller_twiml(scenario)},
    )
    resp.raise_for_status()
    caller_sid = resp.json()["sid"]
    print(f"caller leg: {caller_sid} — waiting for completion")
    for _ in range(40):
        time.sleep(5)
        status = (
            client.get(f"{API}/Accounts/{account}/Calls/{caller_sid}.json").json().get("status")
        )
        print(f"  status={status}")
        if status in ("completed", "failed", "busy", "no-answer", "canceled"):
            break
    print("waiting 20s for recording availability")
    time.sleep(20)
    # The INBOUND leg at the sim number is the one the machine handled.
    resp = client.get(
        f"{API}/Accounts/{account}/Calls.json",
        params={"To": args.number, "PageSize": 5},
    )
    resp.raise_for_status()
    inbound = [c for c in resp.json().get("calls", []) if c["direction"].startswith("inbound")]
    if not inbound:
        sys.exit("no inbound leg found at the sim number")
    return _grade_call(client, account, inbound[0]["sid"])


def grade_call_detail(
    client: httpx.Client,
    account: str,
    call_sid: str,
    *,
    expected_voicemail_message: str | None = None,
    allowed_identity_name: str | None = None,
    ignore_message_content: bool = False,
) -> dict | None:
    """Grade one sim-side leg and return the full call record, or None.

    ``_grade_call`` wraps this for the CLI's exit-code contract; batch callers
    (the benchmark suite) need the structured analysis instead.
    """
    doc = client.get(f"{SYNC}/Documents/call-{call_sid}")
    if doc.status_code != 200:
        print(f"{call_sid}: no sim state doc (was this a sim call?)")
        return None
    call_state = doc.json()["data"]
    out_dir = PSTN_RESULTS_DIR / call_sid
    out_dir.mkdir(parents=True, exist_ok=True)

    recordings = (
        client.get(f"{API}/Accounts/{account}/Recordings.json", params={"CallSid": call_sid})
        .json()
        .get("recordings", [])
    )
    files: list[str] = []
    mailbox_files: list[str] = []
    recording_metadata: list[dict] = []
    for i, rec in enumerate(sorted(recordings, key=lambda r: r["date_created"])):
        media = client.get(f"https://api.twilio.com{rec['uri'].replace('.json', '.wav')}")
        if media.status_code == 200:
            source = str(rec.get("source") or "unknown")
            safe_source = "".join(ch if ch.isalnum() else "-" for ch in source).strip("-").lower()
            path = out_dir / f"recording-{i}-{safe_source or 'unknown'}.wav"
            path.write_bytes(media.content)
            files.append(str(path))
            if source.lower() == "recordverb":
                mailbox_files.append(str(path))
            recording_metadata.append(
                {
                    "sid": rec.get("sid"),
                    "source": source,
                    "channels": rec.get("channels"),
                    "duration": rec.get("duration"),
                    "file": str(path),
                }
            )
    call = {
        "call_sid": call_sid,
        "scenario": call_state.get("scenario"),
        "digits": call_state.get("digits", []),
        "recording_files": files,
        # Only post-beep/post-prompt <Record> audio is valid for timing and
        # message-content grading. Full-call recordings are audit evidence.
        "analysis_recording_files": mailbox_files,
        "recordings": recording_metadata,
    }
    if expected_voicemail_message:
        call["expected_voicemail_message"] = expected_voicemail_message
    raw = _apply_expected_voicemail_override(
        _load_scenario(call["scenario"]),
        expected_voicemail_message=expected_voicemail_message,
        allowed_identity_name=allowed_identity_name,
    )
    if ignore_message_content:
        raw = dict(raw)
        raw["expect"] = dict(raw.get("expect") or {})
        raw["expect"].pop("message_content", None)
    analysis = analyze_call(call, raw, compiled=None)
    call["analysis"] = analysis
    call["artifacts_dir"] = str(out_dir)
    (out_dir / "call.json").write_text(json.dumps(call, indent=2))
    return call


def _print_grade(call: dict) -> None:
    analysis = call["analysis"]
    print(f"\nscenario={call['scenario']} call={call['call_sid']} passed={analysis['passed']}")
    for check in analysis["checks"]:
        flag = "✓" if check["passed"] else "✗"
        print(f"  {flag} {check['check']}: {check['detail']}")
    if analysis.get("skipped_webhook_side"):
        print(f"  (webhook-side checks to verify in CI data: {analysis['skipped_webhook_side']})")
    print(f"artifacts → {call['artifacts_dir']}")


def _grade_call(
    client: httpx.Client,
    account: str,
    call_sid: str,
    *,
    expected_voicemail_message: str | None = None,
    allowed_identity_name: str | None = None,
    ignore_message_content: bool = False,
) -> int:
    call = grade_call_detail(
        client,
        account,
        call_sid,
        expected_voicemail_message=expected_voicemail_message,
        allowed_identity_name=allowed_identity_name,
        ignore_message_content=ignore_message_content,
    )
    if call is None:
        return 1
    _print_grade(call)
    return 0 if call["analysis"]["passed"] else 1


def cmd_grade(args) -> int:
    client, account = _client()
    if args.call_sid:
        return _grade_call(client, account, args.call_sid)
    resp = client.get(
        f"{API}/Accounts/{account}/Calls.json",
        params={"To": args.number, "PageSize": args.last},
    )
    resp.raise_for_status()
    rc = 0
    for call in resp.json().get("calls", []):
        if call["direction"].startswith("inbound"):
            rc |= _grade_call(client, account, call["sid"])
    return rc


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--number", required=True)
    q.add_argument("--scenario", required=True)
    s = sub.add_parser("smoke")
    s.add_argument("--number", required=True)
    s.add_argument("--scenario", required=True)
    s.add_argument("--from-number")
    g = sub.add_parser("grade")
    g.add_argument("--call-sid")
    g.add_argument("--number")
    g.add_argument("--last", type=int, default=3)
    args = parser.parse_args()
    if args.cmd == "queue":
        return cmd_queue(args)
    if args.cmd == "smoke":
        return cmd_smoke(args)
    return cmd_grade(args)


if __name__ == "__main__":
    raise SystemExit(main())
