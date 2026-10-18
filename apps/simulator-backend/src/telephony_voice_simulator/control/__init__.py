"""Local-first control plane for the telephony_voice_simulator.

The simulator package is the product.  This module exposes its provider-neutral
application service through a CLI and an optional HTTP API; the web console is
just one client of that API.
"""

from .store import SimulatorStore

__all__ = ["SimulatorService", "SimulatorStore"]


def __getattr__(name: str):
    # Provider modules import control.models before the application service is ready.
    if name == "SimulatorService":
        from .service import SimulatorService
        return SimulatorService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
