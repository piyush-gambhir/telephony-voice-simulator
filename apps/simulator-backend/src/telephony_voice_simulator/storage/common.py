"""Identifiers, timestamps and conflicts shared by repository operations."""

import uuid
from datetime import UTC, datetime


class StoreConflictError(RuntimeError):
    """A repository write conflicts with an existing record."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
