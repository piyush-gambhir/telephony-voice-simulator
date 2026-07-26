"""Validation helpers for public and explicitly local PSTN runtimes."""

from __future__ import annotations

import ipaddress
from urllib.parse import SplitResult, urlsplit


def _parsed_http_url(value: str) -> SplitResult | None:
    try:
        parsed = urlsplit(value)
        # Accessing ``port`` validates malformed values such as ``:not-a-port``.
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed


def is_absolute_https_url(value: str) -> bool:
    parsed = _parsed_http_url(value.strip())
    return parsed is not None and parsed.scheme == "https"


def is_loopback_host(value: str) -> bool:
    host = value.strip().split("%", 1)[0]
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_http_url(value: str) -> bool:
    parsed = _parsed_http_url(value.strip())
    return (
        parsed is not None
        and parsed.scheme == "http"
        and is_loopback_host(parsed.hostname or "")
    )


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}
