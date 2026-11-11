"""Progress callbacks and application errors for customer operations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...integrations import elevenlabs
from ..errors import ConflictError, ValidationError

Progress = Callable[[str], None]


def _quiet(_line: str) -> None:
    pass


def _translated(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except elevenlabs.ElevenLabsError as exc:
        raise ConflictError(str(exc)) from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
