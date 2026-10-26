"""The application service shared by the CLI and the HTTP API."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..domains.catalog import ScenarioCatalog
from ..paths import RESULTS_DIR
from ..storage.sqlite import SimulatorStore
from ..telephony import ProviderAdapter, provider_registry
from ..telephony.twilio.management import TwilioNumberManager
from .calls import CallsMixin
from .numbers import NumbersMixin
from .resources import ResourcesMixin
from .runs import RunsMixin


class SimulatorService(ResourcesMixin, NumbersMixin, RunsMixin, CallsMixin):
    """Validates input, persists through the store and dispatches through providers."""

    def __init__(
        self,
        store: SimulatorStore | None = None,
        catalog: ScenarioCatalog | None = None,
        recordings_root: str | Path | None = None,
        twilio_numbers: TwilioNumberManager | None = None,
        providers: Mapping[str, ProviderAdapter] | None = None,
    ) -> None:
        self.store = store or SimulatorStore()
        self.catalog = catalog or ScenarioCatalog()
        self.providers = dict(providers) if providers is not None else provider_registry(self.store)
        for key, provider in self.providers.items():
            if key != provider.descriptor.key:
                raise ValueError(f"Provider registry key {key} does not match its descriptor")
        self.recordings_root = Path(recordings_root or RESULTS_DIR / "pstn").resolve()
        self.twilio_numbers = twilio_numbers or TwilioNumberManager()
