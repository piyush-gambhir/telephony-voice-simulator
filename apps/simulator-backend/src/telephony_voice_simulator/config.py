"""Validation helpers for public and explicitly local PSTN runtimes."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from .corpus.release_policy import ReleasePolicyError, validate_public_pstn_assets
from .paths import ASSETS_DIR, CORPUS_MANIFEST, SCENARIOS_DIR


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


def validate_serve_configuration(host: str, *, include_amd: bool = False) -> None:
    """Refuse to start Twilio webhooks that would be unsigned or serve unapproved audio."""
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
