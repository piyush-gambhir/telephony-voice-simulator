"""Provider-neutral telephony composition boundary.

The control plane imports only this package. Carrier implementations remain
independent modules under :mod:`telephony.providers`.
"""

from .base import ProviderAdapter, ProviderError, ProviderRuntimeStore
from .registry import provider_registry

__all__ = [
    "ProviderAdapter",
    "ProviderError",
    "ProviderRuntimeStore",
    "provider_registry",
]
