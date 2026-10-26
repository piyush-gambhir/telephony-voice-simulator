"""Composition root for independent telephony provider modules."""

from __future__ import annotations

from .base import ProviderAdapter, ProviderRuntimeStore
from .mock import MockProvider
from .telnyx import TelnyxProvider
from .twilio import LocalTwilioRuntime, TwilioProvider


def provider_registry(store: ProviderRuntimeStore) -> dict[str, ProviderAdapter]:
    providers: list[ProviderAdapter] = [
        MockProvider(),
        TwilioProvider(store=store, runtime=LocalTwilioRuntime()),
        TelnyxProvider(),
    ]
    return {provider.descriptor.key: provider for provider in providers}
