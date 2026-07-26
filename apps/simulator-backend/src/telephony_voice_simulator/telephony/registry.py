"""Composition root for independent telephony provider modules."""

from __future__ import annotations

from .base import ProviderAdapter, ProviderRuntimeStore
from .providers.mock import MockProvider
from .providers.telnyx import TelnyxProvider
from .providers.twilio import LocalTwilioRuntime, TwilioProvider


def provider_registry(store: ProviderRuntimeStore) -> dict[str, ProviderAdapter]:
    providers: list[ProviderAdapter] = [
        MockProvider(),
        TwilioProvider(store=store, runtime=LocalTwilioRuntime()),
        TelnyxProvider(),
    ]
    return {provider.descriptor.key: provider for provider in providers}
