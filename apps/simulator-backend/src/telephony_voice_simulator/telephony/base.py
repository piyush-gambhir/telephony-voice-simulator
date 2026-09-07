"""Contracts shared by independent telephony provider modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..control.models import (
    Endpoint,
    ProviderConnection,
    ProviderDescriptor,
)


class ProviderError(RuntimeError):
    """A provider cannot perform the requested operation."""


class ProviderRuntimeStore(Protocol):
    """Minimal persistence port available to carrier adapters.

    Providers can remember ownership and manage their own durable run queues
    without importing SQLite, MySQL, or a concrete repository implementation.
    """

    def list_endpoints(self) -> list[Endpoint]: ...

    def get_connection(self, connection_id: str) -> ProviderConnection | None: ...

    def remember_provider_address(self, provider: str, address: str) -> None: ...

    def enqueue_provider_run(
        self,
        *,
        provider: str,
        address: str,
        scenario: str,
        run_id: str | None,
    ) -> None: ...

    def fail_provider_queue(self, provider: str, address: str, reason: str) -> int: ...


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    connection: ProviderConnection
    endpoint: Endpoint
    scenario: dict[str, Any]


@dataclass(frozen=True)
class RunDispatch:
    status: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    completed: bool = False


class ProviderAdapter(ABC):
    """Carrier/module contract consumed by the common control plane."""

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor:
        raise NotImplementedError

    def runtime_status(self, connection: ProviderConnection | None = None) -> dict[str, Any]:
        return {"ready": True, "message": "Ready"}

    def validate_connection(self, connection: ProviderConnection) -> None:
        if connection.provider != self.descriptor.key:
            raise ProviderError(
                f"Connection {connection.id} belongs to {connection.provider}, "
                f"not {self.descriptor.key}"
            )

    def supports_scenario(self, scenario: dict[str, Any]) -> bool:
        return scenario.get("kind") in self.descriptor.supported_scenario_kinds

    def endpoint_identity(self, kind: str, address: str) -> str:
        """Return the provider-specific identity used for uniqueness checks."""

        return address.strip()

    def validate_endpoint(
        self,
        store: ProviderRuntimeStore,
        *,
        kind: str,
        address: str,
        endpoint_id: str | None = None,
    ) -> None:
        """Validate one endpoint without leaking carrier rules into the service."""

        if kind not in self.descriptor.endpoint_kinds:
            raise ProviderError(f"{self.descriptor.name} does not support {kind}")

    def register_endpoint(
        self,
        store: ProviderRuntimeStore,
        endpoint: Endpoint,
    ) -> None:
        """Record provider ownership after a successful endpoint write."""

    def invalidate_pending_runs(
        self,
        store: ProviderRuntimeStore,
        endpoint: Endpoint,
        reason: str,
    ) -> int:
        """Fail provider-owned queued work made stale by a configuration change."""

        return 0

    @abstractmethod
    async def dispatch(self, request: RunRequest) -> RunDispatch:
        raise NotImplementedError
