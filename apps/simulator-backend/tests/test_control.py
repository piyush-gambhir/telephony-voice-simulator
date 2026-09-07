"""Control-plane tests: store, provider contract, service, and optional API."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.control.api import build_app
from telephony_voice_simulator.control.catalog import ScenarioCatalog
from telephony_voice_simulator.control.service import SimulatorService, ValidationError
from telephony_voice_simulator.control.store import SimulatorStore
from telephony_voice_simulator.telephony.base import ProviderError
from telephony_voice_simulator.telephony.providers.mock import MockProvider


class FakeTwilioNumberManager:
    def __init__(self) -> None:
        self.numbers = [
            {
                "sid": "PN-amd-one",
                "phone_number": "+14255550101",
                "friendly_name": "amd-simulator-line",
                "voice_url": "https://old-sim.twil.io/machine?mode=voice",
                "voice_method": "POST",
                "capabilities": {"voice": True},
            },
            {
                "sid": "PN-other",
                "phone_number": "+14255550102",
                "friendly_name": "unrelated",
                "voice_url": "https://example.test/inbound",
                "voice_method": "POST",
                "capabilities": {"voice": True},
            },
        ]

    async def list_numbers(self):
        return self.numbers

    async def update_number(
        self,
        provider_resource_id: str,
        *,
        friendly_name: str | None = None,
        voice_url: str | None = None,
        voice_method: str = "POST",
    ):
        number = next(item for item in self.numbers if item["sid"] == provider_resource_id)
        if friendly_name is not None:
            number["friendly_name"] = friendly_name
        if voice_url is not None:
            number["voice_url"] = voice_url
            number["voice_method"] = voice_method
        return number


@pytest.fixture()
def service(tmp_path: Path) -> SimulatorService:
    return SimulatorService(store=SimulatorStore(tmp_path / "telephony_voice_simulator.db"))


def test_custom_provider_validates_profiles_before_persistence(tmp_path: Path) -> None:
    class RegionProvider(MockProvider):
        def validate_connection(self, connection):
            super().validate_connection(connection)
            if connection.settings.get("region") != "local":
                raise ProviderError("Region must be local")

    service = SimulatorService(
        store=SimulatorStore(tmp_path / "custom.db"), providers={"mock": RegionProvider()}
    )
    with pytest.raises(ValidationError, match="Region must be local"):
        service.create_connection("mock", "Invalid", {"region": "remote"})
    assert service.list_connections() == []

    created = service.create_connection("mock", "Original", {"region": "local"})
    with pytest.raises(ValidationError, match="Region must be local"):
        service.update_connection(created["id"], name="Changed", settings={"region": "remote"})
    assert service.list_connections() == [created]


def test_catalog_validation_collects_bad_and_duplicate_files(tmp_path: Path) -> None:
    (tmp_path / "one.yaml").write_text("name: example\nexpect: {}\nmachine:\n  main:\n    - hangup: true\n")
    (tmp_path / "two.yaml").write_text("name: example\nexpect: {}\nmachine:\n  main:\n    - hangup: true\n")
    (tmp_path / "invalid.yaml").write_text("name: invalid\nmachine:\n  main:\n    - wait: -1\n")
    catalog = ScenarioCatalog(tmp_path)
    report = catalog.validate()
    assert report["valid"] is False
    assert len(report["scenarios"]) == 3
    errors = [entry["error"] for entry in report["scenarios"] if not entry["valid"]]
    assert any("Duplicate scenario name" in error for error in errors)
    assert any("nonnegative" in error for error in errors)
    with pytest.raises(ValueError, match="nonnegative"):
        catalog.get("invalid")
    (tmp_path / "invalid.yaml").unlink()
    with pytest.raises(ValueError, match="Duplicate scenario name"):
        catalog.list()


def test_catalog_reports_dtmf_behavior_and_default_tone_values() -> None:
    catalog = ScenarioCatalog()
    entry = catalog.describe({
        "name": "dtmf_gate",
        "machine": {
            "main": [{"tone": None}],
            "on_dtmf": {"digits": ["1"], "switch": "answered"},
            "answered": [{"hangup": True}],
        },
    })
    assert entry["has_dtmf"] is True
    assert entry["sequences"][0]["steps"][0]["frequency_hz"] == 1000
    assert entry["sequences"][0]["steps"][0]["duration_s"] == 0.5


@pytest.mark.parametrize("timeout", [True, 5.9, None, [], "25.5"])
def test_ring_timeout_never_silently_truncates(service: SimulatorService, timeout) -> None:
    connection = service.create_connection("mock", "Local")
    with pytest.raises(ValidationError, match="whole number"):
        service.create_directory_entry(
            connection_id=connection["id"], extension="1501", name="Desk",
            destination="+15550101501", ring_timeout=timeout,
        )
    assert service.list_directory_entries() == []


async def test_run_api_cancels_only_unclaimed_queue_assignments(service: SimulatorService) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"], name="Queue", kind="extension", address="1001"
    )
    queued = []
    for _ in range(2):
        run = service.store.create_run(endpoint["id"], "mock", "human")
        service.store.update_run(run, status="queued", result={"mode": "pstn"})
        service.store.enqueue_provider_run(
            provider="mock", address=endpoint["address"], scenario=run.scenario, run_id=run.id
        )
        queued.append(run)
    async with TestClient(TestServer(build_app(service))) as client:
        response = await client.post(f"/api/runs/{queued[0].id}/cancel")
        assert response.status == 200
        cancelled = await response.json()
        assert cancelled["status"] == "cancelled"
        assert cancelled["completed_at"]
        assert cancelled["result"]["mode"] == "pstn"
        assert service.store.list_provider_queue("mock", endpoint["address"]) == [
            {"scenario": "human", "run_id": queued[1].id}
        ]
        repeated = await client.post(f"/api/runs/{queued[0].id}/cancel")
        assert await repeated.json() == cancelled
        assert await (await client.get(f"/api/runs/{queued[0].id}")).json() == cancelled
        assert (await client.get("/api/runs/missing")).status == 404

        service.store.dequeue_provider_run("mock", endpoint["address"])
        conflict = await client.post(
            f"/api/runs/{queued[1].id}/cancel", headers={"Origin": "http://localhost:3000"}
        )
        assert conflict.status == 409
        assert conflict.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert service.store.get_run(queued[1].id).status == "queued"


@pytest.mark.parametrize("field,value", [("name", None), ("provider", []), ("description", 10)])
async def test_api_rejects_wrong_types_without_creating_profiles(
    service: SimulatorService, field: str, value,
) -> None:
    async with TestClient(TestServer(build_app(service))) as client:
        body = {"provider": "mock", "name": "Valid", field: value}
        response = await client.post("/api/providers", json=body)
        assert response.status == 400
        assert (await response.json())["error"] == f"{field} must be a string"
    assert service.list_connections() == []


@pytest.mark.parametrize("field", ["connection_id", "kind", "routing_mode"])
def test_endpoint_updates_reject_empty_configuration_instead_of_ignoring_it(
    service: SimulatorService, field: str,
) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"], name="Desk", kind="extension", address="1001"
    )
    with pytest.raises(ValidationError):
        service.update_endpoint(endpoint["id"], **{field: ""})
    assert service.list_endpoints() == [endpoint]


async def test_amd_number_validates_before_applying_carrier_changes(tmp_path: Path) -> None:
    manager = FakeTwilioNumberManager()
    service = SimulatorService(store=SimulatorStore(tmp_path / "numbers.db"), twilio_numbers=manager)
    number = (await service.sync_amd_numbers())[0]
    original_name = manager.numbers[0]["friendly_name"]
    with pytest.raises(ValidationError, match="Unknown scenario"):
        await service.update_amd_number(
            number["id"], friendly_name="Do not apply", default_scenario="missing"
        )
    assert manager.numbers[0]["friendly_name"] == original_name
    assert service.list_amd_numbers()[0]["friendly_name"] == original_name


async def test_mock_provider_run_is_available_without_ui_or_credentials(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    scenario = service.list_scenarios()[0]
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Test extension",
        kind="extension",
        address="1001",
        default_scenario=scenario["name"],
    )

    run = await service.create_run(endpoint["id"])

    assert run["status"] == "completed"
    assert run["provider"] == "mock"
    assert run["result"]["mode"] == "dry_run"
    assert run["result"]["graded"] is False
    assert run["timeline"]
    assert service.list_runs()[0]["id"] == run["id"]


def test_directory_crud_and_free_form_ivr_simulation(service: SimulatorService) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Public line",
        kind="phone_number",
        address="+15550100001",
    )
    entry = service.create_directory_entry(
        connection_id=connection["id"],
        extension="1501",
        name="Front desk",
        destination="+15550101501",
        department="Reception",
        ring_timeout=30,
    )

    run = service.create_ivr_simulation(
        endpoint_id=endpoint["id"],
        caller_number="+14085550131",
        extension="1501",
        disposition="busy",
    )

    assert service.list_directory_entries()[0]["extension"] == "1501"
    assert run["status"] == "completed"
    assert run["result"]["outcome"] == "dial_busy"
    assert run["result"]["destination"] == "+15550101501"
    assert run["caller_number"] == "+14085550131"
    assert run["extension"] == "1501"
    assert run["destination"] == "+15550101501"
    assert run["outcome"] == "busy"
    assert run["duration_seconds"] == 8
    assert any(step["kind"] == "dtmf" for step in run["timeline"])

    service.delete_directory_entry(entry["id"])
    assert service.list_directory_entries() == []


def test_free_form_ivr_unknown_extension_is_a_failed_run(service: SimulatorService) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Public line",
        kind="phone_number",
        address="+15550100001",
    )

    run = service.create_ivr_simulation(
        endpoint_id=endpoint["id"],
        caller_number="+14085550131",
        extension="9999",
        disposition="connected",
    )

    assert run["result"]["outcome"] == "failed"
    assert run["result"]["destination"] is None
    assert run["outcome"] == "failed"
    assert run["duration_seconds"] == 7


def test_free_form_ivr_supports_abandoned_and_no_answer_duration(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Public line",
        kind="phone_number",
        address="+15550100001",
    )
    service.create_directory_entry(
        connection_id=connection["id"],
        extension="1501",
        name="Front desk",
        destination="+15550101501",
        department="Reception",
        ring_timeout=37,
    )

    abandoned = service.create_ivr_simulation(
        endpoint_id=endpoint["id"],
        caller_number="+14085550131",
        extension="1501",
        disposition="abandoned",
    )
    no_answer = service.create_ivr_simulation(
        endpoint_id=endpoint["id"],
        caller_number="+14085550131",
        extension="1501",
        disposition="no_answer",
    )

    assert abandoned["outcome"] == "abandoned"
    assert abandoned["duration_seconds"] == 12
    assert abandoned["result"]["outcome"] == "abandoned"
    assert abandoned["timeline"][-1]["actor"] == "caller"
    assert no_answer["outcome"] == "no_answer"
    assert no_answer["duration_seconds"] == 44
    assert no_answer["timeline"][-1]["at"] == "00:44"
    assert no_answer["result"]["outcome"] == "dial_no_answer"


def test_endpoint_and_directory_records_can_be_updated_and_deleted(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Old line",
        kind="phone_number",
        address="+15550100001",
    )
    entry = service.create_directory_entry(
        connection_id=connection["id"],
        extension="1501",
        name="Old name",
        destination="+15550101501",
    )

    endpoint = service.update_endpoint(
        endpoint["id"],
        name="Updated line",
        address="+15550100002",
        routing_mode="queued",
        default_scenario="connect-1501",
        enabled=False,
    )
    entry = service.update_directory_entry(
        entry["id"],
        extension="1502",
        name="Updated name",
        destination="+15550101502",
        department="Service",
        ring_timeout=45,
        enabled=False,
    )

    assert endpoint["name"] == "Updated line"
    assert endpoint["routing_mode"] == "queued"
    assert endpoint["default_scenario"] == "connect-1501"
    assert endpoint["enabled"] is False
    assert entry["extension"] == "1502"
    assert entry["ring_timeout"] == 45
    assert entry["enabled"] is False

    service.delete_endpoint(endpoint["id"])
    assert service.list_endpoints() == []


async def test_endpoint_routing_mode_controls_scenario_override(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    default_scenario, requested_scenario = [
        item["name"] for item in service.list_scenarios()[:2]
    ]
    fixed = service.create_endpoint(
        connection_id=connection["id"],
        name="Fixed line",
        kind="phone_number",
        address="+15550100001",
        routing_mode="fixed",
        default_scenario=default_scenario,
    )
    queued = service.create_endpoint(
        connection_id=connection["id"],
        name="Queued line",
        kind="phone_number",
        address="+15550100002",
        routing_mode="queued",
        default_scenario=default_scenario,
    )

    fixed_run = await service.create_run(fixed["id"], requested_scenario)
    queued_run = await service.create_run(queued["id"], requested_scenario)

    assert fixed_run["scenario"] == default_scenario
    assert queued_run["scenario"] == requested_scenario


async def test_disabled_endpoints_cannot_run_catalog_or_free_form_calls(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Disabled public line",
        kind="phone_number",
        address="+15550100001",
        default_scenario=service.list_scenarios()[0]["name"],
    )
    service.update_endpoint(endpoint["id"], enabled=False)

    with pytest.raises(ValidationError, match="endpoint is disabled"):
        await service.create_run(endpoint["id"])
    with pytest.raises(ValidationError, match="endpoint is disabled"):
        service.create_ivr_simulation(
            endpoint_id=endpoint["id"],
            caller_number="+14085550131",
            extension="1501",
            disposition="connected",
        )


def test_store_additively_migrates_legacy_simulation_run_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE provider_connections (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                name TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE endpoints (
                id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL REFERENCES provider_connections(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                address TEXT NOT NULL,
                routing_mode TEXT NOT NULL DEFAULT 'fixed',
                default_scenario TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(connection_id, address)
            );
            CREATE TABLE simulation_runs (
                id TEXT PRIMARY KEY,
                endpoint_id TEXT NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                scenario TEXT NOT NULL,
                status TEXT NOT NULL,
                timeline_json TEXT NOT NULL DEFAULT '[]',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            """
        )

    store = SimulatorStore(path)
    with store._connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(simulation_runs)")}
        provider_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(provider_connections)")
        }
        tables = {
            row["name"]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {
        "caller_number",
        "extension",
        "destination",
        "outcome",
        "duration_seconds",
    } <= columns
    assert {"status", "description"} <= provider_columns
    assert "deleted_incoming_calls" in tables


def test_endpoint_kind_is_validated_by_provider(service: SimulatorService) -> None:
    connection = service.create_connection("twilio", "Twilio local")

    with pytest.raises(ValidationError, match="does not support extension"):
        service.create_endpoint(
            connection_id=connection["id"],
            name="Unsupported",
            kind="extension",
            address="1001",
        )


async def test_twilio_scenario_compatibility_is_enforced_by_service(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("twilio", "Twilio local")
    amd = next(item for item in service.list_scenarios() if item["kind"] == "amd")
    ivr = next(item for item in service.list_scenarios() if item["kind"] == "ivr")

    with pytest.raises(ValidationError, match="does not support ivr scenarios"):
        service.create_endpoint(
            connection_id=connection["id"],
            name="Invalid default",
            kind="phone_number",
            address="+15550100001",
            default_scenario=ivr["name"],
        )

    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Twilio line",
        kind="phone_number",
        address="+15550100001",
        default_scenario=amd["name"],
    )
    with pytest.raises(ValidationError, match="does not support ivr scenarios"):
        service.update_endpoint(endpoint["id"], default_scenario=ivr["name"])
    queued = service.create_endpoint(
        connection_id=connection["id"],
        name="Queued Twilio line",
        kind="phone_number",
        address="+15550100002",
        routing_mode="queued",
        default_scenario=amd["name"],
    )
    with pytest.raises(ValidationError, match="does not support ivr scenarios"):
        await service.create_run(queued["id"], ivr["name"])
    assert service.list_runs() == []


def test_twilio_endpoints_require_unique_canonical_e164(
    service: SimulatorService,
) -> None:
    first = service.create_connection("twilio", "Primary")
    second = service.create_connection("twilio", "Secondary")
    service.create_endpoint(
        connection_id=first["id"],
        name="Primary line",
        kind="phone_number",
        address="+15550100001",
    )

    with pytest.raises(ValidationError, match="canonical E.164"):
        service.create_endpoint(
            connection_id=second["id"],
            name="Formatted duplicate",
            kind="phone_number",
            address="+1 (555) 010-0001",
        )
    with pytest.raises(ValidationError, match="already managed"):
        service.create_endpoint(
            connection_id=second["id"],
            name="Conflicting route",
            kind="phone_number",
            address="+15550100001",
        )


def test_directory_requires_four_digits_and_dialable_destination(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    for extension in ("123", "12345", "12ab"):
        with pytest.raises(ValidationError, match="exactly 4 digits"):
            service.create_directory_entry(
                connection_id=connection["id"],
                extension=extension,
                name="Invalid",
                destination="+15550101501",
            )
    with pytest.raises(ValidationError, match="E.164 number or sip:/sips: URI"):
        service.create_directory_entry(
            connection_id=connection["id"],
            extension="1501",
            name="Invalid destination",
            destination="555-010-1501",
        )
    sip = service.create_directory_entry(
        connection_id=connection["id"],
        extension="1501",
        name="SIP desk",
        destination="sips:desk@example.test",
    )
    assert sip["destination"] == "sips:desk@example.test"


def test_twilio_provider_descriptor_is_fail_closed_for_number_management(
    service: SimulatorService,
) -> None:
    twilio = next(item for item in service.provider_catalog() if item["key"] == "twilio")

    assert twilio["endpoint_kinds"] == ["phone_number"]
    assert twilio["capabilities"]["number_management"] is True
    assert twilio["capabilities"]["sip"] is False


def test_endpoint_mutation_fails_and_removes_stale_pstn_queue(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("twilio", "Twilio local")
    scenario = next(item for item in service.list_scenarios() if item["kind"] == "amd")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Queued line",
        kind="phone_number",
        address="+15550100001",
        routing_mode="queued",
        default_scenario=scenario["name"],
    )
    run = service.store.create_run(endpoint["id"], "twilio", scenario["name"])
    service.store.enqueue_pstn_run(
        address=endpoint["address"],
        scenario=scenario["name"],
        run_id=run.id,
    )

    service.update_endpoint(endpoint["id"], enabled=False)

    assert service.store.list_pstn_queue(endpoint["address"]) == []
    failed = service.store.get_run(run.id)
    assert failed is not None
    assert failed.status == "failed"
    assert "routing changed" in failed.result["error"]


def test_provider_disable_fails_and_removes_all_stale_pstn_queues(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("twilio", "Twilio local")
    scenario = next(item for item in service.list_scenarios() if item["kind"] == "amd")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Queued line",
        kind="phone_number",
        address="+15550100001",
        routing_mode="queued",
        default_scenario=scenario["name"],
    )
    run = service.store.create_run(endpoint["id"], "twilio", scenario["name"])
    service.store.enqueue_pstn_run(
        address=endpoint["address"],
        scenario=scenario["name"],
        run_id=run.id,
    )

    service.update_connection(connection["id"], status="disabled")

    assert service.store.list_pstn_queue(endpoint["address"]) == []
    failed = service.store.get_run(run.id)
    assert failed is not None and failed.status == "failed"
    assert "connection was disabled" in failed.result["error"]


def test_telnyx_adapter_is_explicitly_preview(service: SimulatorService) -> None:
    telnyx = next(item for item in service.provider_catalog() if item["key"] == "telnyx")

    assert telnyx["status"] == "preview"
    assert telnyx["runtime"]["ready"] is False


def test_provider_profiles_preserve_status_and_description(
    service: SimulatorService,
) -> None:
    connection = service.create_connection(
        "twilio",
        "Staging carrier",
        status="needs_setup",
        description="Waiting for a public webhook URL.",
    )
    disabled = service.create_connection("mock", "Disabled profile", status="disabled")

    assert connection["status"] == "needs_setup"
    assert connection["description"] == "Waiting for a public webhook URL."
    assert disabled["status"] == "disabled"
    assert disabled["enabled"] is False


def test_provider_profiles_can_be_updated(service: SimulatorService) -> None:
    connection = service.create_connection(
        "twilio",
        "Staging carrier",
        status="needs_setup",
        description="Waiting for credentials.",
        settings={"region": "us1"},
    )

    updated = service.update_connection(
        connection["id"],
        name="Production carrier",
        status="ready",
        description="Configured.",
        settings={"region": "ie1"},
    )

    assert updated["provider"] == "twilio"
    assert updated["name"] == "Production carrier"
    assert updated["status"] == "ready"
    assert updated["description"] == "Configured."
    assert updated["settings"] == {"region": "ie1"}
    assert updated["enabled"] is True

    disabled = service.update_connection(connection["id"], status="disabled")
    assert disabled["enabled"] is False


async def test_control_api_round_trip(service: SimulatorService) -> None:
    client = TestClient(TestServer(build_app(service)))
    await client.start_server()
    try:
        response = await client.post(
            "/api/providers", json={"provider": "mock", "name": "Browser local"}
        )
        assert response.status == 201
        connection = await response.json()

        scenario_response = await client.get("/api/scenarios")
        scenarios = await scenario_response.json()
        response = await client.post(
            "/api/endpoints",
            json={
                "connection_id": connection["id"],
                "name": "Local line",
                "kind": "phone_number",
                "address": "+15550100001",
                "default_scenario": scenarios[0]["name"],
            },
        )
        assert response.status == 201
        endpoint = await response.json()

        response = await client.post(
            "/api/runs",
            json={"endpoint_id": endpoint["id"], "scenario": scenarios[0]["name"]},
        )
        assert response.status == 201
        run = await response.json()
        assert run["status"] == "completed"
    finally:
        await client.close()


async def test_control_api_exposes_provider_endpoint_and_directory_crud(
    service: SimulatorService,
) -> None:
    client = TestClient(TestServer(build_app(service)))
    await client.start_server()
    try:
        response = await client.post(
            "/api/providers",
            json={
                "provider": "mock",
                "name": "Needs review",
                "status": "needs_setup",
                "description": "Created through the control API.",
                "settings": {"region": "local"},
            },
        )
        assert response.status == 201
        connection = await response.json()
        assert connection["status"] == "needs_setup"
        assert connection["description"] == "Created through the control API."

        response = await client.patch(
            f"/api/providers/{connection['id']}",
            json={
                "name": "Ready local",
                "status": "ready",
                "description": "Updated through the control API.",
            },
        )
        assert response.status == 200
        connection = await response.json()
        assert connection["name"] == "Ready local"
        assert connection["status"] == "ready"

        response = await client.post(
            "/api/endpoints",
            json={
                "connection_id": connection["id"],
                "name": "Old public line",
                "kind": "phone_number",
                "address": "+15550100001",
            },
        )
        assert response.status == 201
        endpoint = await response.json()

        response = await client.patch(
            f"/api/endpoints/{endpoint['id']}",
            json={
                "name": "Updated public line",
                "routing_mode": "queued",
                "enabled": False,
            },
        )
        assert response.status == 200
        endpoint = await response.json()
        assert endpoint["name"] == "Updated public line"
        assert endpoint["routing_mode"] == "queued"
        assert endpoint["enabled"] is False

        response = await client.post(
            "/api/directory",
            json={
                "connection_id": connection["id"],
                "extension": "1501",
                "name": "Old front desk",
                "destination": "+15550101501",
            },
        )
        assert response.status == 201
        entry = await response.json()

        response = await client.patch(
            f"/api/directory/{entry['id']}",
            json={
                "extension": "1502",
                "name": "Updated front desk",
                "department": "Reception",
                "ring_timeout": 40,
                "enabled": False,
            },
        )
        assert response.status == 200
        entry = await response.json()
        assert entry["extension"] == "1502"
        assert entry["ring_timeout"] == 40
        assert entry["enabled"] is False

        response = await client.delete(f"/api/directory/{entry['id']}")
        assert response.status == 204
        response = await client.delete(f"/api/endpoints/{endpoint['id']}")
        assert response.status == 204
        assert "does not detach" in response.headers["Warning"]
        response = await client.delete(f"/api/providers/{connection['id']}")
        assert response.status == 204

        invalid = await client.post(
            "/api/providers",
            json={"provider": "mock", "name": "Invalid", "settings": ["not", "an", "object"]},
        )
        assert invalid.status == 400
    finally:
        await client.close()


async def test_deprecated_simulations_alias_preserves_legacy_shape(
    service: SimulatorService,
) -> None:
    connection = service.create_connection("mock", "Local")
    endpoint = service.create_endpoint(
        connection_id=connection["id"],
        name="Public line",
        kind="phone_number",
        address="+15550100001",
    )
    service.create_directory_entry(
        connection_id=connection["id"],
        extension="1501",
        name="Front desk",
        destination="+15550101501",
    )
    client = TestClient(TestServer(build_app(service)))
    await client.start_server()
    try:
        response = await client.post(
            "/api/simulations",
            json={
                "endpointId": endpoint["id"],
                "callerNumber": "+14085550131",
                "extension": "1501",
                "disposition": "connected",
            },
        )
        assert response.status == 200
        assert response.headers["Deprecation"] == "true"
        payload = await response.json()
        assert set(payload) == {"run", "steps"}
        assert payload["run"]["result"]["outcome"] == "bridged"
        assert payload["steps"]
    finally:
        await client.close()


def test_ivr_only_app_does_not_load_or_expose_amd_assets(
    service: SimulatorService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    monkeypatch.setattr(pstn_server.STATE, "scenarios", {})

    def fail_if_loaded() -> None:
        raise AssertionError("IVR-only mode must not load the AMD corpus")

    monkeypatch.setattr(pstn_server.STATE, "load_scenarios", fail_if_loaded)
    app = build_app(service, include_ivr=True)
    paths = {resource.canonical for resource in app.router.resources()}

    assert "/twilio/ivr" in paths
    assert "/twilio/voice" not in paths
    assert "/assets" not in paths


async def test_combined_runtime_uses_injected_store(
    service: SimulatorService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    compiled = tmp_path / "compiled"
    compiled.mkdir()
    monkeypatch.setattr(pstn_server, "COMPILED_DIR", compiled)
    monkeypatch.setattr(pstn_server.STATE, "scenarios", {"already-loaded": object()})
    monkeypatch.setattr(pstn_server, "CALL_STORE", None)

    client = TestClient(TestServer(build_app(service, include_twilio=True)))
    await client.start_server()
    try:
        assert pstn_server.CALL_STORE is service.store
        health = await client.get("/health")
        assert health.status == 200

        calls = await client.get("/api/calls")
        assert calls.status == 200
    finally:
        await client.close()


async def test_combined_call_deletion_evicts_raw_pstn_state(
    service: SimulatorService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telephony_voice_simulator.pstn import server as pstn_server

    call_id = "CA-combined-delete"
    compiled = tmp_path / "compiled-combined"
    compiled.mkdir()
    monkeypatch.setattr(pstn_server, "COMPILED_DIR", compiled)
    monkeypatch.setattr(pstn_server.STATE, "scenarios", {"already-loaded": object()})
    pstn_server.STATE.deleted_call_ids.discard(call_id)
    pstn_server.STATE.calls[call_id] = {
        "call_sid": call_id,
        "status": "completed",
        "scenario": "already-loaded",
    }
    service.store.upsert_incoming_call(call_id=call_id, status="completed")

    client = TestClient(TestServer(build_app(service, include_twilio=True)))
    await client.start_server()
    try:
        response = await client.delete(f"/api/calls/{call_id}")
        assert response.status == 200
        assert call_id not in pstn_server.STATE.calls
        assert call_id in pstn_server.STATE.deleted_call_ids
        assert service.store.is_incoming_call_deleted(call_id)

    finally:
        await client.close()
        pstn_server.STATE.calls.pop(call_id, None)
        pstn_server.STATE.deleted_call_ids.discard(call_id)


async def test_call_history_and_recording_media_are_served(
    service: SimulatorService, tmp_path: Path
) -> None:
    recording_root = tmp_path / "results" / "pstn"
    call_directory = recording_root / "CA-history"
    call_directory.mkdir(parents=True)
    audio = call_directory / "full-call.wav"
    audio.write_bytes(b"RIFF-local-test")
    local_service = SimulatorService(
        store=service.store,
        recordings_root=recording_root,
    )
    local_service.store.upsert_incoming_call(
        call_id="CA-history",
        from_address="+12223334444",
        to_address="+15550100001",
        scenario="stock_voicemail_beep_1000",
        status="completed",
        recording_status="completed",
    )
    local_service.store.upsert_recording(
        recording_id="RE-history",
        call_id="CA-history",
        provider="twilio",
        kind="full_call",
        status="completed",
        local_path=str(audio),
    )
    client = TestClient(TestServer(build_app(local_service)))
    await client.start_server()
    try:
        response = await client.get("/api/calls")
        calls = await response.json()
        assert calls[0]["id"] == "CA-history"
        assert calls[0]["recordings"][0]["kind"] == "full_call"

        response = await client.get("/api/recordings/RE-history/media")
        assert response.status == 200
        assert await response.read() == b"RIFF-local-test"
    finally:
        await client.close()


async def test_recording_media_rejects_paths_outside_the_call_results_directory(
    tmp_path: Path,
) -> None:
    recording_root = tmp_path / "results" / "pstn"
    call_directory = recording_root / "CA-confined"
    call_directory.mkdir(parents=True)
    external_audio = tmp_path / "private.txt"
    external_audio.write_text("not simulator recording media")
    symlink_audio = call_directory / "escaped.wav"
    symlink_audio.symlink_to(external_audio)

    service = SimulatorService(
        store=SimulatorStore(tmp_path / "confined.db"),
        recordings_root=recording_root,
    )
    service.store.upsert_incoming_call(call_id="CA-confined", status="completed")
    service.store.upsert_recording(
        recording_id="RE-direct-external",
        call_id="CA-confined",
        provider="twilio",
        kind="message",
        status="completed",
        local_path=str(external_audio),
    )
    service.store.upsert_recording(
        recording_id="RE-symlink-external",
        call_id="CA-confined",
        provider="twilio",
        kind="message",
        status="completed",
        local_path=str(symlink_audio),
    )

    client = TestClient(TestServer(build_app(service)))
    await client.start_server()
    try:
        direct = await client.get("/api/recordings/RE-direct-external/media")
        symlink = await client.get("/api/recordings/RE-symlink-external/media")
        assert direct.status == 404
        assert symlink.status == 404
        assert external_audio.read_text() == "not simulator recording media"
    finally:
        await client.close()


async def test_call_deletion_erases_only_simulator_owned_recordings(tmp_path: Path) -> None:
    recording_root = tmp_path / "results" / "pstn"
    call_directory = recording_root / "CA-delete"
    call_directory.mkdir(parents=True)
    owned_audio = call_directory / "full-call.wav"
    owned_audio.write_bytes(b"private-owned-audio")
    call_report = call_directory / "call.json"
    call_report.write_text('{"transcript": "private call artifact"}')
    external_audio = tmp_path / "external.wav"
    external_audio.write_bytes(b"externally-managed-audio")

    service = SimulatorService(
        store=SimulatorStore(tmp_path / "calls.db"),
        recordings_root=recording_root,
    )
    service.store.upsert_incoming_call(
        call_id="CA-delete",
        from_address="+12223334444",
        to_address="+15550100001",
        status="completed",
        recording_status="completed",
    )
    service.store.upsert_recording(
        recording_id="RE-owned",
        call_id="CA-delete",
        provider="twilio",
        kind="full_call",
        status="completed",
        local_path=str(owned_audio),
    )
    service.store.upsert_recording(
        recording_id="RE-external",
        call_id="CA-delete",
        provider="twilio",
        kind="message",
        status="completed",
        local_path=str(external_audio),
    )

    client = TestClient(TestServer(build_app(service)))
    await client.start_server()
    try:
        response = await client.delete("/api/calls/CA-delete")
        assert response.status == 200
        result = await response.json()
        assert result == {
            "deleted": "CA-delete",
            "already_deleted": False,
            "recording_files": {
                "deleted": 1,
                "missing": 0,
                "skipped_unowned": 1,
            },
            "external_provider_recordings": {
                "retained": 2,
                "providers": ["twilio"],
                "note": (
                    "This deletion removes simulator metadata and local artifacts only. "
                    "Provider-hosted recordings follow provider retention and must be "
                    "deleted through the provider separately."
                ),
            },
            "artifacts_directory_deleted": True,
        }
        assert not owned_audio.exists()
        assert not call_report.exists()
        assert not call_directory.exists()
        assert external_audio.read_bytes() == b"externally-managed-audio"
        assert service.store.get_incoming_call("CA-delete") is None
        assert service.store.is_incoming_call_deleted("CA-delete")
        assert service.store.get_recording("RE-owned") is None
        assert service.store.get_recording("RE-external") is None

        repeated = await client.delete("/api/calls/CA-delete")
        assert repeated.status == 200
        assert (await repeated.json())["already_deleted"] is True
    finally:
        await client.close()


def test_call_deletion_tombstones_before_artifact_cleanup_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telephony_voice_simulator.control import service as service_module

    call_id = "CA-cleanup-retry"
    recording_root = tmp_path / "results" / "pstn"
    call_directory = recording_root / call_id
    call_directory.mkdir(parents=True)
    (call_directory / "call.json").write_text('{"private": true}')
    service = SimulatorService(
        store=SimulatorStore(tmp_path / "cleanup-retry.db"),
        recordings_root=recording_root,
    )
    service.store.upsert_incoming_call(call_id=call_id, status="completed")

    real_rmtree = service_module.shutil.rmtree

    def fail_cleanup(_path: Path) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(service_module.shutil, "rmtree", fail_cleanup)
    with pytest.raises(ValidationError, match="tombstoned"):
        service.delete_call(call_id)

    assert service.store.get_incoming_call(call_id) is None
    assert service.store.is_incoming_call_deleted(call_id)
    assert call_directory.exists()

    monkeypatch.setattr(service_module.shutil, "rmtree", real_rmtree)
    retried = service.delete_call(call_id)

    assert retried["already_deleted"] is True
    assert retried["artifacts_directory_deleted"] is True
    assert not call_directory.exists()


async def test_amd_number_inventory_is_persisted_configured_and_queued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeTwilioNumberManager()
    service = SimulatorService(
        store=SimulatorStore(tmp_path / "amd-numbers.db"),
        twilio_numbers=manager,
    )
    imported = await service.sync_amd_numbers()

    assert len(imported) == 1
    number = imported[0]
    assert number["phone_number"] == "+14255550101"
    assert number["endpoint"]["routing_mode"] == "queued"
    assert number["endpoint"]["default_scenario"] == "stock_voicemail_beep_1000"
    assert len(service.list_connections()) == 1
    assert len(service.list_endpoints()) == 1

    renamed = await service.update_amd_number(
        number["id"],
        friendly_name="Telephony Voice Simulator · AMD 01",
        default_scenario="google_voice_voicemail",
        routing_mode="queued",
        record_full_calls=True,
    )
    assert renamed["friendly_name"] == "Telephony Voice Simulator · AMD 01"
    assert renamed["endpoint"]["name"] == "Telephony Voice Simulator · AMD 01"
    assert renamed["endpoint"]["default_scenario"] == "google_voice_voicemail"
    assert renamed["configuration"]["record_full_calls"] is True
    from telephony_voice_simulator.telephony.providers.twilio import webhooks

    monkeypatch.setattr(webhooks, "CALL_STORE", service.store)
    assert webhooks._full_call_recording_enabled(number["phone_number"]) is True

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://voice-simulator.example")
    attached = await service.attach_amd_number(number["id"])
    assert attached["attached_to_runtime"] is True
    assert attached["configuration"]["previous_voice_url"] == (
        "https://old-sim.twil.io/machine?mode=voice"
    )

    service.providers["twilio"].runtime.scenario_available = lambda _name: True
    queued = await service.queue_amd_number(number["id"], "google_voice_voicemail")
    assert queued["run"]["status"] == "queued"
    assert queued["number"]["pending_runs"] == 1

    restored = await service.restore_amd_number(number["id"])
    assert restored["voice_url"] == "https://old-sim.twil.io/machine?mode=voice"
    assert restored["attached_to_runtime"] is False
