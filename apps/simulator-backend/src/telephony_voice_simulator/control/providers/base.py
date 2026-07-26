"""Compatibility imports for the new common telephony boundary."""

from ...telephony.base import (
    ProviderAdapter,
    ProviderError,
    ProviderRuntimeStore,
    RunDispatch,
    RunRequest,
)

__all__ = [
    "ProviderAdapter",
    "ProviderError",
    "ProviderRuntimeStore",
    "RunDispatch",
    "RunRequest",
]
