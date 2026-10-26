"""Runs: dispatch a scenario to a line, IVR directory simulations, cancellation."""

from __future__ import annotations

import re
from typing import Any

from ..domains.ivr import Destination, simulate_directory_call
from ..storage.sqlite import StoreConflictError
from ..telephony import ProviderError
from ..telephony.base import RunRequest
from .errors import ConflictError, NotFoundError, ValidationError


class RunsMixin:
    def list_runs(self) -> list[dict[str, Any]]:
        return [run.as_dict() for run in self.store.list_runs()]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Unknown run: {run_id}")
        return run.as_dict()

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Unknown run: {run_id}")
        if run.status == "cancelled":
            return run.as_dict()
        try:
            cancelled = self.store.cancel_queued_run(run_id)
        except StoreConflictError as exc:
            raise ConflictError(str(exc)) from exc
        if cancelled is None:
            raise ConflictError("Only queued runs that have not been claimed can be cancelled")
        return cancelled.as_dict()

    async def create_run(
        self, endpoint_id: str, scenario_name: str | None = None
    ) -> dict[str, Any]:
        endpoint = self.store.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValidationError("Select a valid endpoint")
        if not endpoint.enabled:
            raise ValidationError("The selected endpoint is disabled")
        connection = self.store.get_connection(endpoint.connection_id)
        if connection is None or not connection.enabled:
            raise ValidationError("The endpoint provider connection is unavailable")
        # A fixed endpoint models a number permanently wired to one behavior:
        # when a default exists, ad-hoc run requests cannot override it. Queued
        # endpoints accept a per-run scenario and fall back to their default.
        selected = (
            endpoint.default_scenario
            if endpoint.routing_mode == "fixed" and endpoint.default_scenario
            else scenario_name or endpoint.default_scenario
        )
        if not selected:
            raise ValidationError("Select a scenario or configure a default on the endpoint")
        provider = self.providers[connection.provider]
        scenario = self._validated_scenario(provider, selected)
        run = self.store.create_run(endpoint.id, connection.provider, selected)
        try:
            dispatched = await provider.dispatch(
                RunRequest(
                    run_id=run.id,
                    connection=connection,
                    endpoint=endpoint,
                    scenario=scenario,
                )
            )
        except ProviderError as exc:
            failed = self.store.update_run(
                run,
                status="failed",
                result={"graded": False, "error": str(exc)},
                completed=True,
            )
            return failed.as_dict()
        updated = self.store.update_run(
            run,
            status=dispatched.status,
            timeline=dispatched.timeline,
            result=dispatched.result,
            completed=dispatched.completed,
        )
        return updated.as_dict()

    def create_ivr_simulation(
        self,
        *,
        endpoint_id: str,
        caller_number: str,
        extension: str,
        disposition: str,
    ) -> dict[str, Any]:
        endpoint = self.store.get_endpoint(endpoint_id)
        if endpoint is None:
            raise ValidationError("Select a valid public endpoint")
        if not endpoint.enabled:
            raise ValidationError("The selected public endpoint is disabled")
        if not 5 <= len(caller_number.strip()) <= 40:
            raise ValidationError("Caller number must contain 5 to 40 characters")
        if not re.fullmatch(r"\d{4}", extension):
            raise ValidationError("Extension must contain exactly 4 digits")
        allowed = {"connected", "busy", "no_answer", "failed", "abandoned"}
        if disposition not in allowed:
            raise ValidationError(
                "Disposition must be connected, busy, no_answer, failed, or abandoned"
            )

        connection = self.store.get_connection(endpoint.connection_id)
        if connection is None or not connection.enabled:
            raise ValidationError("The endpoint provider connection is unavailable")
        entry = self.store.get_directory_entry_by_extension(extension)
        route_connection = (
            self.store.get_connection(entry.connection_id) if entry is not None else None
        )
        destination = (
            Destination(entry.name, entry.destination)
            if entry is not None
            and entry.enabled
            and route_connection is not None
            and route_connection.enabled
            else None
        )
        steps, outcome = simulate_directory_call(
            caller_number=caller_number.strip(),
            public_number=endpoint.address,
            extension=extension,
            destination=destination,
            ring_timeout=entry.ring_timeout if entry else 25,
            forced_disposition=disposition,
        )
        call_outcome = {
            "bridged": "connected",
            "dial_busy": "busy",
            "dial_no_answer": "no_answer",
            "dial_failed": "failed",
            "failed": "failed",
            "abandoned": "abandoned",
        }[outcome]
        # A model run ends at its final timeline event (including time spent in
        # the menu). Do not invent a conversation length after the bridge.
        minutes, seconds = steps[-1].at.split(":")
        duration_seconds = int(minutes) * 60 + int(seconds)
        run = self.store.create_run(
            endpoint.id,
            route_connection.provider if destination and route_connection else connection.provider,
            "ivr_custom",
            caller_number=caller_number.strip(),
            extension=extension,
            destination=entry.destination if destination and entry else None,
            outcome=call_outcome,
            duration_seconds=duration_seconds,
        )
        completed = self.store.update_run(
            run,
            status="completed",
            timeline=[
                {**step.as_dict(index), "state": "simulated", "sequence": "main"}
                for index, step in enumerate(steps)
            ],
            result={
                "graded": False,
                "mode": "ivr_directory",
                "summary": f"Directory simulation completed with outcome: {outcome}.",
                "outcome": outcome,
                "call_outcome": call_outcome,
                "caller_number": caller_number.strip(),
                "extension": extension,
                "destination": entry.destination if destination and entry else None,
                "duration_seconds": duration_seconds,
            },
            completed=True,
        )
        return completed.as_dict()
