from __future__ import annotations

from telephony_voice_simulator.control.catalog import ScenarioCatalog
from telephony_voice_simulator.domains.ivr import get_scenario as get_ivr_scenario
from telephony_voice_simulator.domains.ivr import SCENARIOS as IVR_SCENARIOS
from telephony_voice_simulator.domains.ivr import simulate as simulate_ivr
from telephony_voice_simulator.domains.pbx import DEPARTMENTS, EXTENSIONS
from telephony_voice_simulator.domains.pbx import get_scenario as get_pbx_scenario
from telephony_voice_simulator.domains.pbx import SCENARIOS as PBX_SCENARIOS
from telephony_voice_simulator.domains.pbx import simulate as simulate_pbx


def test_every_ivr_scenario_reaches_its_expected_outcome() -> None:
    for scenario in IVR_SCENARIOS:
        steps, outcome = simulate_ivr(scenario)
        assert outcome == scenario.expected_outcome, scenario.name
        assert steps


def test_ivr_retry_and_bridge_timeline() -> None:
    scenario = get_ivr_scenario("retry-after-wrong-extension")
    assert scenario is not None
    steps, outcome = simulate_ivr(scenario)
    assert outcome == "bridged"
    assert any(step.kind == "retry" for step in steps)
    assert any(step.kind == "dtmf" for step in steps)
    assert steps[-1].kind == "bridge"


def test_every_pbx_scenario_reaches_its_expected_outcome() -> None:
    for scenario in PBX_SCENARIOS:
        steps, outcome = simulate_pbx(scenario)
        assert outcome == scenario.expected_outcome, scenario.name
        assert steps


def test_pbx_overflow_and_after_hours_paths() -> None:
    overflow = get_pbx_scenario("service-overflow")
    after_hours = get_pbx_scenario("after-hours-voicemail")
    assert overflow is not None
    assert after_hours is not None

    overflow_steps, overflow_outcome = simulate_pbx(overflow)
    voicemail_steps, voicemail_outcome = simulate_pbx(after_hours)

    assert overflow_outcome == "overflow"
    assert any(step.kind == "overflow" for step in overflow_steps)
    assert voicemail_outcome == "voicemail"
    assert not any(step.actor == "queue" for step in voicemail_steps)
    assert any(step.actor == "crm" and step.kind == "callback" for step in voicemail_steps)
    assert voicemail_steps[-1].kind == "cdr"


def test_catalog_exposes_all_scenario_kinds() -> None:
    catalog = ScenarioCatalog()
    entries = catalog.list()
    assert {"amd", "ivr", "pbx"} <= {entry["kind"] for entry in entries}
    assert catalog.get("ivr_connect_extension")["simulation_only"] is True
    assert catalog.get("connect-1501")["name"] == "connect-1501"
    assert catalog.get("retry-after-silence")["expected_outcome"] == "bridged"
    assert catalog.get("ivr_destination_failed")["name"] == "destination-failed"
    assert catalog.get("stock_voicemail_beep_1000")["simulation_only"] is False


def test_pbx_inventory_and_metadata_match_the_full_model() -> None:
    assert len(DEPARTMENTS) == 6
    assert {department.id for department in DEPARTMENTS} == {
        "reception",
        "sales",
        "service",
        "parts",
        "finance",
        "accounting",
    }
    assert len(EXTENSIONS) == 8
    assert {extension.ext for extension in EXTENSIONS} == {
        "100",
        "201",
        "202",
        "301",
        "302",
        "401",
        "501",
        "601",
    }
    assert all(extension.role and extension.device for extension in EXTENSIONS)

    scenario = get_pbx_scenario("sales-mainline-open")
    assert scenario is not None
    steps, outcome = simulate_pbx(scenario)
    assert outcome == "connected"
    assert steps[0].metadata["caller_number"] == scenario.caller_number
    assert steps[0].metadata["intent"] == scenario.intent
    assert all(step.metadata["leg"] and step.metadata["destination"] for step in steps)
    ringing = next(step for step in steps if step.kind == "ring")
    assert ringing.metadata["role"]
    assert ringing.metadata["device"]
    assert any(step.actor == "crm" and step.kind == "activity" for step in steps)
    assert steps[-1].kind == "cdr"


def test_pbx_runtime_state_overrides_change_the_route() -> None:
    scenario = get_pbx_scenario("sales-mainline-open")
    assert scenario is not None
    steps, outcome = simulate_pbx(scenario, {"sales-a": "busy", "sales-b": "offline"})

    assert outcome == "overflow"
    assert any(step.kind == "overflow" for step in steps)
    assert any(step.actor == "crm" and step.kind == "callback" for step in steps)


def test_unified_scenario_ids_remain_resolvable_as_aliases() -> None:
    assert get_ivr_scenario("ivr_connect_extension").name == "connect-1501"
    assert get_ivr_scenario("ivr_retry_after_silence").name == "retry-after-silence"
    assert get_pbx_scenario("pbx_service_queue_overflow").name == "service-overflow"
