"""Canonical public telephony identifiers used by every control surface."""

from __future__ import annotations

import re

_E164 = re.compile(r"\+[1-9]\d{7,14}")
_SIP_URI = re.compile(r"sips?:[^\s@]+@[^\s@]+", re.IGNORECASE)
_E164_FORMATTING = str.maketrans("", "", " ()-.")


def is_canonical_e164(value: str) -> bool:
    """Return whether *value* is an exact, dialable E.164 address."""

    return _E164.fullmatch(value) is not None


def normalize_e164(value: str) -> str | None:
    """Normalize harmless legacy display punctuation for address comparison.

    New writes must use :func:`is_canonical_e164`; this helper exists so an
    older formatted managed number cannot become unmanaged and fail open.
    """

    candidate = value.strip()
    if not candidate.startswith("+"):
        return None
    candidate = candidate.translate(_E164_FORMATTING)
    return candidate if is_canonical_e164(candidate) else None


def is_sip_uri(value: str) -> bool:
    return _SIP_URI.fullmatch(value) is not None


def is_directory_destination(value: str) -> bool:
    return is_canonical_e164(value) or is_sip_uri(value)
