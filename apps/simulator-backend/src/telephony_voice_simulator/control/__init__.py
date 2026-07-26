"""Local-first control plane for the telephony_voice_simulator.

The simulator package is the product.  This module exposes its provider-neutral
application service through a CLI and an optional HTTP API; the web console is
just one client of that API.
"""

from .service import SimulatorService
from .store import SimulatorStore

__all__ = ["SimulatorService", "SimulatorStore"]
