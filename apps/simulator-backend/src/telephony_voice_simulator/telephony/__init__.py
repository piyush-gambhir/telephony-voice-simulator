"""Provider-neutral telephony composition boundary.

Run dispatch uses this package's adapter contract and provider registry.
Carrier implementations live in the mock, Telnyx and Twilio modules.
"""

from .base import ProviderAdapter, ProviderError, ProviderRuntimeStore
from .registry import provider_registry

__all__ = [
    "ProviderAdapter",
    "ProviderError",
    "ProviderRuntimeStore",
    "provider_registry",
]
