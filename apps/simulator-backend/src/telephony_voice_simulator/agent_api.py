"""Drive a voice agent through its own REST API (provider-neutral adapter).

``agent_call.py`` dispatches a LiveKit worker directly, which bypasses whatever
control plane normally configures a call. This adapter is the opposite trade:
it asks the agent's *own* backend to place the outbound call, so the run
exercises the real prompt, model, telephony and answering-machine configuration
that production would resolve for that agent — the simulator is just the callee.

Any platform that can (a) place an outbound call over HTTP and (b) read a call
record back by id fits. Nothing here names a vendor; the request body and the
response field locations are supplied as configuration.

Environment:
  SIM_NUMBER                     the simulator line the agent must dial
  AGENT_API_TRIGGER_URL          POST endpoint that places the call
  AGENT_API_TRIGGER_METHOD       default POST
  AGENT_API_AUTHORIZATION        Authorization header value, sent verbatim
  AGENT_API_BODY                 path to a JSON body template (see below)
  AGENT_API_CALL_ID_PATH         dotted path to the call id      (default callId)
  AGENT_API_RECORD_URL           GET template containing {call_id}
  AGENT_API_RECORD_AUTHORIZATION defaults to AGENT_API_AUTHORIZATION
  AGENT_API_STATUS_PATH          dotted path to call status      (default status)
  AGENT_API_TERMINAL_STATUSES    comma list (default completed,ended,failed,
                                 no-answer,busy,canceled)
  AGENT_API_ENDED_REASON_PATH    dotted path to the fine-grained end reason
  AGENT_API_DETECTION_LAYER_PATH dotted path to the machine-detection layer
  AGENT_API_AMD_PATH             dotted path to the AMD verdict object

The body template is JSON with ``{sim_number}``, ``{scenario}`` and
``{run_id}`` substituted into string values (recursively, including inside
nested objects and arrays).

Usage:
    python -m telephony_voice_simulator.agent_api trigger --scenario stock_voicemail_beep_1000
    python -m telephony_voice_simulator.agent_api record --call-id 019f8e3c-...
    python -m telephony_voice_simulator.agent_api call --scenario dtmf_gate_honored
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_TERMINAL_STATUSES = (
    "completed",
    "ended",
    "failed",
    "no-answer",
    "no_answer",
    "busy",
    "canceled",
    "cancelled",
)


def dotted(obj: Any, path: str) -> Any:
    """Read ``a.b.c`` out of nested dicts/lists; None when any hop is missing."""
    if not path:
        return None
    cursor = obj
    for part in path.split("."):
        if isinstance(cursor, list):
            try:
                if int(part) < 0:
                    return None
                cursor = cursor[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _first_dotted(obj: Any, paths: list[str]) -> Any:
    for path in paths:
        value = dotted(obj, path)
        if value not in (None, ""):
            return value
    return None


def render(value: Any, variables: dict[str, str]) -> Any:
    """Substitute ``{name}`` placeholders through a nested JSON structure."""
    if isinstance(value, str):
        out = value
        for key, replacement in variables.items():
            out = out.replace("{" + key + "}", replacement)
        return out
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    return value


@dataclass
class AgentApiConfig:
    trigger_url: str
    body_template: dict[str, Any]
    record_url: str = ""
    trigger_method: str = "POST"
    authorization: str = ""
    record_authorization: str = ""
    call_id_path: str = "callId"
    status_path: str = "status"
    terminal_statuses: tuple[str, ...] = DEFAULT_TERMINAL_STATUSES
    ended_reason_paths: list[str] = field(default_factory=list)
    detection_layer_paths: list[str] = field(default_factory=list)
    amd_paths: list[str] = field(default_factory=list)
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.body_template, dict):
            raise ValueError("agent API body template must be a JSON object")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("agent API timeout must be positive and finite")
        self.terminal_statuses = tuple(s.strip().lower() for s in self.terminal_statuses)
        if not self.terminal_statuses or any(not s for s in self.terminal_statuses):
            raise ValueError("at least one nonempty terminal status is required")
        if self.record_url and "{call_id}" not in self.record_url:
            raise ValueError("agent API record URL must contain {call_id}")

    @property
    def record_headers(self) -> dict[str, str]:
        # An empty record authorization inherits the trigger's, which is the
        # common case. Read paths are often unauthenticated while the write
        # path is not, so "none" explicitly suppresses the header instead.
        if self.record_authorization.lower() == "none":
            return {}
        auth = self.record_authorization or self.authorization
        return {"Authorization": auth} if auth else {}

    @property
    def trigger_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        return headers


def _split_paths(name: str, fallback: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()] if raw else fallback


def load_config(*, require_trigger: bool = True) -> AgentApiConfig:
    trigger_url = os.environ.get("AGENT_API_TRIGGER_URL", "").strip()
    if require_trigger and not trigger_url:
        sys.exit("Set AGENT_API_TRIGGER_URL (see the module docstring).")
    body_path = os.environ.get("AGENT_API_BODY", "").strip()
    if require_trigger and not body_path:
        sys.exit("Set AGENT_API_BODY to a JSON request-body template.")
    body = {}
    if body_path:
        template_file = Path(body_path).expanduser()
        if not template_file.is_file():
            sys.exit(f"AGENT_API_BODY not found: {template_file}")
        body = json.loads(template_file.read_text())
    if not isinstance(body, dict):
        raise ValueError("AGENT_API_BODY must contain a JSON object")
    body.pop("_comment", None)

    statuses = os.environ.get("AGENT_API_TERMINAL_STATUSES", "").strip()
    return AgentApiConfig(
        trigger_url=trigger_url,
        body_template=body,
        record_url=os.environ.get("AGENT_API_RECORD_URL", "").strip(),
        trigger_method=os.environ.get("AGENT_API_TRIGGER_METHOD", "POST").upper(),
        authorization=os.environ.get("AGENT_API_AUTHORIZATION", "").strip(),
        record_authorization=os.environ.get("AGENT_API_RECORD_AUTHORIZATION", "").strip(),
        call_id_path=os.environ.get("AGENT_API_CALL_ID_PATH", "callId").strip(),
        status_path=os.environ.get("AGENT_API_STATUS_PATH", "status").strip(),
        terminal_statuses=(
            tuple(s.strip().lower() for s in statuses.split(",") if s.strip())
            if statuses
            else DEFAULT_TERMINAL_STATUSES
        ),
        ended_reason_paths=_split_paths(
            "AGENT_API_ENDED_REASON_PATH", ["endedReason", "ended_reason"]
        ),
        detection_layer_paths=_split_paths(
            "AGENT_API_DETECTION_LAYER_PATH",
            ["voicemailDetectionLayer", "voicemail_detection_layer"],
        ),
        amd_paths=_split_paths("AGENT_API_AMD_PATH", ["amdResult", "amd_result"]),
    )


def sim_number() -> str:
    number = os.environ.get("SIM_NUMBER", "").strip()
    if not number:
        sys.exit("Set SIM_NUMBER to the simulator line the agent should dial.")
    return number


def trigger_call(
    config: AgentApiConfig,
    *,
    scenario: str,
    number: str,
    run_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Place the outbound call. Returns ``(call_id, raw_response)``."""
    run_id = run_id or f"sim-{uuid.uuid4().hex[:12]}"
    body = render(
        config.body_template,
        {"sim_number": number, "scenario": scenario, "run_id": run_id},
    )
    response = httpx.request(
        config.trigger_method,
        config.trigger_url,
        json=body,
        headers=config.trigger_headers,
        timeout=config.timeout,
    )
    if not response.is_success:
        # The body frequently carries the actionable reason (bad mapping id,
        # expired token, unroutable number); a bare status code does not.
        raise RuntimeError(f"trigger failed {response.status_code}: {response.text[:400]}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("agent trigger response must be a JSON object")
    call_id = _first_dotted(payload, [config.call_id_path]) or ""
    if not call_id or isinstance(call_id, (dict, list, bool)):
        raise RuntimeError(
            f"no call id at '{config.call_id_path}' in trigger response: "
            f"{json.dumps(payload)[:400]}"
        )
    return str(call_id), payload


def fetch_record(config: AgentApiConfig, call_id: str) -> dict[str, Any] | None:
    if not config.record_url:
        return None
    url = config.record_url.replace("{call_id}", quote(call_id, safe=""))
    response = httpx.get(url, headers=config.record_headers, timeout=config.timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("agent call record must be a JSON object")
    # Control planes commonly wrap the resource in {data: ...}; unwrap only
    # when the envelope has no status of its own, so a genuine `data` field on
    # the record itself is never mistaken for an envelope.
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("data"), dict)
        and dotted(payload, config.status_path) is None
    ):
        return payload["data"]
    return payload


def is_terminal(config: AgentApiConfig, record: dict[str, Any] | None) -> bool:
    return str(dotted(record, config.status_path) or "").strip().lower() in config.terminal_statuses


def wait_for_terminal(
    config: AgentApiConfig,
    call_id: str,
    *,
    timeout: float = 300.0,
    interval: float = 10.0,
    progress: bool = True,
    label: str = "",
) -> dict[str, Any] | None:
    """Poll the call record until its status is terminal. None on timeout."""
    if not config.record_url:
        return None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("poll timeout must be positive and finite")
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("poll interval must be positive and finite")
    deadline = time.monotonic() + timeout
    record: dict[str, Any] | None = None
    previous: str | None = None
    while time.monotonic() < deadline:
        try:
            record = fetch_record(config, call_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {408, 429} and exc.response.status_code < 500:
                raise
        except httpx.TransportError:
            pass
        status = str(dotted(record, config.status_path) or "").strip().lower()
        # Only on CHANGE: a repeated status carries no information, and with
        # several lanes polling at once the repeats interleave into a wall of
        # text that buries the actual results.
        if progress and status != previous:
            prefix = f"{label} " if label else "    "
            print(f"{prefix}{status or '(pending)':9} call_id={call_id}", flush=True)
            previous = status
        if is_terminal(config, record):
            return record
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    return None


def extract_outcome(config: AgentApiConfig, record: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the agent's own verdict out of its call record."""
    if not record:
        return {
            "status": "",
            "ended_reason": "",
            "detection_layer": "",
            "amd": None,
            "is_machine": None,
            "detection_delay": None,
        }
    amd = _first_dotted(record, config.amd_paths)
    layer = _first_dotted(record, config.detection_layer_paths) or ""
    if not layer and isinstance(amd, dict) and amd.get("is_machine") is True:
        # Platforms that don't publish a single layer string usually expose the
        # detector and its reason separately; "<detector>:<reason>" reproduces
        # the prefix shape the scenario expectations match on.
        #
        # Only ever synthesized for a MACHINE verdict. A detector that ran and
        # concluded "human" has no detection layer, and inventing one from its
        # reason string fails every `detection_layer_absent` precision guard —
        # reporting the agent's correct answer as a miss.
        detector = str(amd.get("detector") or "")
        reason = str(amd.get("reason") or "")
        layer = f"{detector}-amd:{reason}" if detector and reason else detector
    is_machine = amd.get("is_machine") if isinstance(amd, dict) else None
    delay = amd.get("delay") if isinstance(amd, dict) else None
    return {
        "status": str(dotted(record, config.status_path) or ""),
        "ended_reason": str(_first_dotted(record, config.ended_reason_paths) or ""),
        "detection_layer": str(layer),
        "amd": amd if isinstance(amd, dict) else None,
        "is_machine": is_machine if isinstance(is_machine, bool) else None,
        "detection_delay": (
            float(delay)
            if type(delay) in (int, float) and math.isfinite(delay) and delay >= 0
            else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("trigger", "call"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--scenario", default="stock_voicemail_beep_1000")
        cmd.add_argument("--number", default="")
        cmd.add_argument("--timeout", type=float, default=300.0)

    rec = sub.add_parser("record")
    rec.add_argument("--call-id", required=True)

    args = parser.parse_args()
    config = load_config(require_trigger=args.cmd != "record")
    if args.cmd in {"call", "record"} and not config.record_url:
        parser.error("AGENT_API_RECORD_URL is required to read call outcomes")

    if args.cmd == "record":
        record = fetch_record(config, args.call_id)
        print(json.dumps({"record": record, "outcome": extract_outcome(config, record)}, indent=2))
        return 0 if record else 1

    number = args.number or sim_number()
    call_id, payload = trigger_call(config, scenario=args.scenario, number=number)
    print(f"triggered call_id={call_id} scenario={args.scenario} -> dials {number}")
    if args.cmd == "trigger":
        print(json.dumps(payload, indent=2))
        return 0

    record = wait_for_terminal(config, call_id, timeout=args.timeout)
    outcome = extract_outcome(config, record)
    print(json.dumps(outcome, indent=2))
    return 0 if record else 1


if __name__ == "__main__":
    sys.exit(main())
