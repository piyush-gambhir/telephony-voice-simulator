"""API routes for the built-in models, scenario validation and the dealer PBX."""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from telephony_voice_simulator.api.app import build_app
from telephony_voice_simulator.service import SimulatorService
from telephony_voice_simulator.storage.sqlite import SimulatorStore


@pytest.fixture()
async def client(tmp_path: Path):
    service = SimulatorService(store=SimulatorStore(tmp_path / "api.db"))
    async with TestClient(TestServer(build_app(service))) as client:
        yield client


async def test_pbx_story_runs_with_extension_overrides(client) -> None:
    response = await client.post(
        "/api/models/simulate",
        json={
            "kind": "pbx",
            "scenario": "pbx_sales_queue_answers",
            "overrides": {"sales-a": "busy", "sales-b": "offline"},
        },
    )
    body = await response.json()
    assert response.status == 200
    assert body["outcome"] == "overflow"


async def test_unknown_extension_override_is_a_400(client) -> None:
    response = await client.post(
        "/api/models/simulate", json={"kind": "pbx", "overrides": {"nope": "busy"}}
    )
    assert response.status == 400
    assert "Unknown PBX extension" in (await response.json())["error"]


async def test_scenario_detail_and_validation(client) -> None:
    listing = await (await client.get("/api/scenarios")).json()
    amd = next(item for item in listing if item["kind"] == "amd")

    detail = await client.get(f"/api/scenarios/{amd['name']}")
    assert (await detail.json())["name"] == amd["name"]
    assert (await client.get("/api/scenarios/not_a_scenario")).status == 404

    report = await (await client.post("/api/scenarios/validate", json={})).json()
    assert report["valid"] is True


async def test_dealer_pbx_routes(client) -> None:
    dealer_map = await (await client.get("/api/pbx/map")).json()
    site = dealer_map["sites"][0]

    call = await client.post(
        "/api/pbx/calls",
        json={"to_number": site["main_did"], "day": "sat", "time": "14:00"},
    )
    assert call.status == 200
    assert "call" in await call.json()

    cases = await (await client.get("/api/pbx/cases")).json()
    case = await client.post(f"/api/pbx/cases/{cases[0]['id']}/run")
    assert (await case.json())["id"] == cases[0]["id"]
    assert (await client.post("/api/pbx/cases/missing/run")).status == 404

    capacity = await client.get(f"/api/pbx/capacity?site={site['id']}&channels=8")
    assert capacity.status == 200
    assert (await client.get("/api/pbx/capacity?channels=zero")).status == 400


async def test_dealer_call_rejects_a_bad_transfer(client) -> None:
    response = await client.post(
        "/api/pbx/calls",
        json={"to_number": "+14155550102", "transfer_method": "blind_refer"},
    )
    assert response.status == 400
