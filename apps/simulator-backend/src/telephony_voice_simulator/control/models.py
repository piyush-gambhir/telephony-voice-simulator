"""Provider-neutral control-plane models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

EndpointKind = Literal["phone_number", "sip_uri", "extension"]
RoutingMode = Literal["fixed", "queued"]


@dataclass(frozen=True)
class ProviderCapabilities:
    inbound_calls: bool = True
    dtmf: bool = False
    recording: bool = False
    bridge: bool = False
    number_management: bool = False
    sip: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderDescriptor:
    key: str
    name: str
    description: str
    status: Literal["ready", "preview"]
    capabilities: ProviderCapabilities
    endpoint_kinds: tuple[EndpointKind, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["endpoint_kinds"] = list(self.endpoint_kinds)
        return data


@dataclass(frozen=True)
class ProviderConnection:
    id: str
    provider: str
    name: str
    status: Literal["ready", "needs_setup", "disabled"] = "ready"
    description: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Endpoint:
    id: str
    connection_id: str
    name: str
    kind: EndpointKind
    address: str
    routing_mode: RoutingMode = "fixed"
    default_scenario: str | None = None
    enabled: bool = True
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderNumber:
    id: str
    provider: str
    connection_id: str
    endpoint_id: str | None
    provider_resource_id: str
    phone_number: str
    friendly_name: str
    voice_url: str
    voice_method: str = "POST"
    status: str = "active"
    managed_mode: str = "amd"
    capabilities: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    last_synced_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DirectoryEntry:
    id: str
    connection_id: str
    extension: str
    name: str
    destination: str
    department: str = "General"
    ring_timeout: int = 25
    enabled: bool = True
    created_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationRun:
    id: str
    endpoint_id: str
    provider: str
    scenario: str
    status: str
    caller_number: str | None = None
    extension: str | None = None
    destination: str | None = None
    outcome: str | None = None
    duration_seconds: int | None = None
    timeline: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    completed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CallRecording:
    id: str
    call_id: str
    provider: str
    kind: Literal["full_call", "message"]
    status: str
    duration_s: float | None = None
    channels: int | None = None
    provider_url: str | None = None
    local_path: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IncomingCall:
    id: str
    provider: str
    direction: Literal["inbound"]
    from_address: str
    to_address: str
    scenario: str | None
    status: str
    recording_status: str
    started_at: str
    ended_at: str | None = None
    duration_s: float | None = None
    analysis: dict[str, Any] = field(default_factory=dict)
    recordings: list[CallRecording] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
