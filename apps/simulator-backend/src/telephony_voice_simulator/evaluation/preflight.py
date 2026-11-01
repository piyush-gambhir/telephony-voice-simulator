"""Preflight for a regression suite: is every webhook and line ready before calls are placed?

A run whose webhooks fail costs real calls and reads as agent failures. The usual causes are
a tunnel that moved while a line still points at the old one, and a server started with a
stale ``PUBLIC_BASE_URL``, which then rejects every Twilio signature. Each check here returns
pass/fail with a reason; ``regression run`` stops on any failure.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import os
from typing import Any

import httpx

from ..telephony.twilio.management import TwilioManagementError, TwilioNumberManager
from .benchmark import execution

# A CallSid of the right shape that Twilio will never issue, for the signed probe.
PROBE_SID = "CA" + "0" * 32
PROBE_LINE = "+15550000000"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": passed, "detail": detail}


def _choices(value: Any) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def suite_lines(suite: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, str]:
    """Each line the cases use, mapped to the webhook path it must point at."""
    lines: dict[str, str] = {}
    for line in _choices(suite.get("persona_lines")) or _choices(suite.get("persona_line")):
        lines[line] = "/twilio/customer"
    for case in cases:
        if case["kind"] == "amd":
            for line in _choices(case.get("line")):
                lines.setdefault(line, "/twilio/voice")
        if target := case.get("target"):
            lines.setdefault(str(target["line"]), "/twilio/voice")
    return lines


def signed_probe(base: str, token: str, account: str) -> tuple[bool, str]:
    """POST a correctly signed request through the public URL to the customer webhook.

    The probe names no persona line, so a server that accepts the signature answers with a
    Reject and stores nothing; a server holding another public URL answers 403.
    """
    url = f"{base}/twilio/customer"
    form = {"CallSid": PROBE_SID, "To": PROBE_LINE, "From": PROBE_LINE,
            "AccountSid": account, "Direction": "inbound"}
    payload = url + "".join(key + form[key] for key in sorted(form))
    signature = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    try:
        resp = httpx.post(url, data=form, headers={"X-Twilio-Signature": signature}, timeout=20)
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if resp.status_code == 403:
        return False, "403: the server checks signatures against another public URL; restart it"
    return resp.status_code == 200, f"HTTP {resp.status_code}"


def run(suite: dict[str, Any], cases: list[dict[str, Any]], store: Any,
        clear: bool = False) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    api = execution.simulator_api()
    try:
        ok = httpx.get(f"{api}/health", timeout=10).status_code == 200
        checks.append(_check("simulator_api", ok, api))
    except httpx.HTTPError as exc:
        checks.append(_check("simulator_api", False, f"{api}: {type(exc).__name__}"))
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base.startswith("https://"):
        checks.append(_check("public_url", False, "PUBLIC_BASE_URL is not an https URL"))
        return checks
    try:
        ok = httpx.get(f"{base}/health", timeout=15).status_code == 200
        checks.append(_check("public_url", ok, base))
    except httpx.HTTPError as exc:
        checks.append(_check("public_url", False, f"{base}: {type(exc).__name__}"))
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    account = os.environ.get("TWILIO_ACCOUNT_SID", "")
    if token and account:
        checks.append(_check("signed_webhook", *signed_probe(base, token, account)))

    lines = suite_lines(suite, cases)
    try:
        numbers = {n.get("phone_number"): n for n in asyncio.run(TwilioNumberManager().list_numbers())}
    except TwilioManagementError as exc:
        checks.append(_check("line_webhooks", False, str(exc)))
        numbers = {}
    if numbers:
        wrong = {line: (numbers.get(line) or {}).get("voice_url") or "not on the account"
                 for line, path in lines.items()
                 if (numbers.get(line) or {}).get("voice_url") != f"{base}{path}"}
        checks.append(_check("line_webhooks", not wrong,
                             f"{len(lines)} lines point at {base}" if not wrong
                             else f"not pointing at this tunnel: {wrong}"))

    personas = _choices(suite.get("persona_lines")) or _choices(suite.get("persona_line"))
    staff = [str(c["target"]["line"]) for c in cases if (c.get("target") or {}).get("persona")]
    from ..service import customer

    try:
        profiles = customer.profiles()
    except Exception as exc:  # an unreadable profiles file fails the check, not the run
        checks.append(_check("persona_lines", False, f"{type(exc).__name__}: {exc}"))
        return checks
    missing = sorted({*personas, *staff} - set(profiles["profiles"]))
    checks.append(_check("persona_lines", not missing,
                         "all listed in PSTN_CUSTOMER_PROFILES" if not missing
                         else f"missing from PSTN_CUSTOMER_PROFILES: {missing}"))
    allowed = set(profiles["allowed_callers"])
    callers = {str(c["agent_number"]) for c in cases if c.get("agent_number")}
    if staff:
        # A transfer reaches a staff line from the customer's number (or the agent's).
        callers |= set(personas)
    if missing_callers := sorted(callers - allowed):
        checks.append(_check("allowed_callers", False,
                             f"not in PSTN_CUSTOMER_ALLOWED_CALLERS: {missing_callers}"))
    else:
        checks.append(_check("allowed_callers", True, f"{len(callers)} callers allowed"))

    leftovers = _leftovers(store, lines, clear)
    checks.append(_check("no_leftover_queues", not leftovers,
                         "nothing queued" if not leftovers else
                         f"left by an earlier run (use --clear): {leftovers}"))
    return checks


def _leftovers(store: Any, lines: dict[str, str], clear: bool) -> dict[str, int]:
    """Personas and scenarios still queued on the suite's lines; cancelled with ``clear``."""
    found: dict[str, int] = {}
    for line in lines:
        personas = store.list_customer_persona_queue(line)
        runs = store.list_provider_queue("twilio", line)
        if clear:
            for item in personas:
                store.cancel_customer_persona(item["id"])
            for item in runs:
                if item.get("run_id"):
                    with contextlib.suppress(Exception):
                        store.cancel_queued_run(item["run_id"])
            continue
        if count := len(personas) + len(runs):
            found[line] = count
    return found
