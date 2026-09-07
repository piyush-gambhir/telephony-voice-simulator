"""Tests for the dealer-group PBX routing and transfer model."""

from __future__ import annotations

from dataclasses import replace

import pytest

from telephony_voice_simulator.pbxsim import (
    CASES,
    COSTS,
    CallRequest,
    TransferAttempt,
    attempt_transfer,
    capacity_report,
    cost_for,
    default_group,
    route,
    run_case,
    run_suite,
    transfer_matrix,
    validate_group,
)
from telephony_voice_simulator.pbxsim.model import CoveragePath, CoveragePoint, Schedule, minutes

GROUP = default_group()

MAIN = "+14155550100"
SERVICE = "+14155550102"
USED_MAIN = "+14155550300"
HONDA_MAIN = "+14155550200"

ALL_SERVICE_BUSY = {"1200": "busy", "1201": "busy", "1202": "busy", "1220": "busy"}
ALL_BDC_BUSY = {"1900": "busy", "1901": "busy", "1902": "busy", "1120": "busy"}


# ------------------------------------------------------------------ suite --- #


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_case_matches_expectation(case) -> None:
    result = run_case(case, GROUP)
    assert result["actual_outcome"] == case.expected_outcome
    if case.expected_transfer is not None:
        assert result["actual_transfer"] == case.expected_transfer


def test_suite_reports_every_case() -> None:
    results = run_suite(GROUP)
    assert len(results) == len(CASES)
    assert all(item["passed"] for item in results)


# ------------------------------------------------------------ config sanity - #


# --------------------------------------------------------------- routing ---- #


def test_routing_is_deterministic() -> None:
    request = CallRequest(to_number=MAIN, digits=("2",))
    first = route(GROUP, request).as_dict()
    second = route(GROUP, request).as_dict()
    assert first == second


def test_time_condition_flips_at_the_boundary() -> None:
    inside = route(GROUP, CallRequest(to_number=SERVICE, day="tue", time="17:59"))
    outside = route(GROUP, CallRequest(to_number=SERVICE, day="tue", time="18:00"))
    assert inside.outcome == "answered_human"
    assert outside.outcome == "mailbox_full"


def test_congestion_when_every_channel_is_taken() -> None:
    result = route(
        GROUP,
        CallRequest(to_number=USED_MAIN, occupied_channels={"used-pri-1": 8}),
    )
    assert result.outcome == "congestion"
    assert result.cdr["answer_seconds"] is None


def test_tandem_consumes_both_tie_trunks() -> None:
    result = route(GROUP, CallRequest(to_number=HONDA_MAIN, digits=("3100",)))
    assert "tie-ford-honda" in result.cdr["trunks"]
    assert "tie-ford-used" in result.cdr["trunks"]


def test_glare_on_immediate_start_loses_digits() -> None:
    result = route(
        GROUP,
        CallRequest(to_number=USED_MAIN, digits=("2",), glare_trunks=("tie-ford-used",)),
    )
    assert "attendant:ford" in result.path
    assert any(event.kind == "digits_lost" for event in result.events)


def test_glare_on_wink_start_is_recoverable() -> None:
    result = route(
        GROUP,
        CallRequest(to_number=MAIN, digits=("2200",), glare_trunks=("tie-ford-honda",)),
    )
    assert not any(event.kind == "digits_lost" for event in result.events)
    assert any(event.kind == "retry" for event in result.events)


def test_tracking_number_is_attributed_but_routes_identically() -> None:
    tracked = route(GROUP, CallRequest(to_number="+14155550150", digits=("1",)))
    plain = route(GROUP, CallRequest(to_number=MAIN, digits=("1",)))
    assert tracked.cdr["tracking_source"] == "paid_search"
    assert tracked.answered_by == plain.answered_by


def test_loop_guard_stops_a_redirect_cycle() -> None:
    from dataclasses import replace

    looped = replace(
        GROUP,
        ivr_menus=tuple(
            replace(menu, options={**menu.options, "1": "ivr:ford-main"})
            if menu.id == "ford-main"
            else menu
            for menu in GROUP.ivr_menus
        ),
    )
    result = route(looped, CallRequest(to_number=MAIN, digits=("1", "1")))
    assert result.outcome == "loop_detected"


# --------------------------------------------------------------- capacity --- #


def test_forwarding_costs_two_carrier_channels_and_peering_costs_one() -> None:
    forwarded = route(GROUP, CallRequest(to_number="+14155550788"))
    peered = route(
        GROUP,
        CallRequest(
            to_number=SERVICE,
            presence={**ALL_SERVICE_BUSY, **ALL_BDC_BUSY},
        ),
    )
    assert forwarded.outcome == "answered_agent"
    assert peered.outcome == "answered_agent"
    assert forwarded.cdr["carrier_channels_used"] == 2
    assert peered.cdr["carrier_channels_used"] == 1


def test_forwarding_fails_when_only_one_channel_remains() -> None:
    result = route(
        GROUP,
        CallRequest(to_number="+14155550788", occupied_channels={"used-pri-1": 7}),
    )
    assert result.outcome == "congestion"
    assert any(event.label == "No channel left to reach the agent" for event in result.events)


def test_registered_agent_still_answers_on_the_last_channel() -> None:
    result = route(
        GROUP,
        CallRequest(
            to_number=SERVICE,
            occupied_channels={"ford-pri-1": 22},
            presence={**ALL_SERVICE_BUSY, **ALL_BDC_BUSY},
        ),
    )
    assert result.outcome == "answered_agent"
    assert result.cdr["carrier_channels_used"] == 1


def test_capacity_report_halves_concurrency_under_forwarding() -> None:
    report = capacity_report(GROUP, "used", channels=8)
    headline = report["headline"]
    assert headline["forwarding_concurrent_calls"] == 4
    assert headline["peering_concurrent_calls"] == 8
    assert headline["capacity_multiplier"] == 2.0
    assert headline["forwarding_concurrent_calls_with_handoff"] == 2


def test_dial_plan_membership_is_what_makes_the_handoff_free() -> None:
    """Two independent rules, and they are what the recommendation turns on."""
    for cost in COSTS:
        # Only a party inside the dial plan can hand a caller back for nothing.
        assert cost.handoff_channels == (0 if cost.inside_dial_plan else 1)
        # Being inside the dial plan also means exactly one carrier leg inbound.
        if cost.inside_dial_plan:
            assert cost.inbound_channels == 1
    # Forwarding is the only pattern that pays twice on the way in, because the
    # switch holds the inbound leg and re-originates a second one alongside it.
    assert cost_for("forwarded_with_diversion").inbound_channels == 2
    assert cost_for("sip_trunk_peer").inbound_channels == 1
    # Carrier-routed never touches the dealership's switch on the way in.
    assert cost_for("carrier_routed").inbound_channels == 0


def test_carrier_routed_is_not_limited_by_the_dealership_trunk() -> None:
    report = capacity_report(GROUP, "used", channels=8)
    rows = {row["ingress"]: row for row in report["rows"]}
    assert rows["carrier_routed"]["concurrent_agent_calls"] is None
    assert rows["carrier_routed"]["limited_by"] == "agent platform"
    assert rows["forwarded_with_diversion"]["limited_by"] == "dealership trunk"
    # It still costs a channel to hand the caller back, since we are outside.
    assert rows["carrier_routed"]["channels_per_call_with_handoff"] == 1


def test_every_configured_agent_has_a_known_channel_cost() -> None:
    for agent in GROUP.agents:
        assert cost_for(agent.ingress) is not None


# -------------------------------------------------------------- transfers --- #


def test_transfer_matrix_reflects_platform_limits() -> None:
    matrix = transfer_matrix(GROUP)
    assert matrix["ford"]["blind_refer"] is False
    assert matrix["honda"]["blind_refer"] is True
    assert matrix["used"]["dtmf_redial"] is False
    assert all(entry["bridge"] for entry in matrix.values())
    assert not any(entry["external_transfer"] for entry in matrix.values())


def test_blind_refer_is_rejected_where_the_sbc_blocks_it() -> None:
    result = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="bdc-overflow",
            method="blind_refer",
            target="ext:1200",
            call=CallRequest(to_number=SERVICE),
        ),
    )
    assert result.outcome == "transfer_rejected"
    assert result.accepted is False
    assert result.alternatives


def test_missing_referred_by_is_rejected() -> None:
    result = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="honda-trunk",
            method="blind_refer",
            target="ext:2300",
            call=CallRequest(to_number=HONDA_MAIN),
            send_referred_by=False,
        ),
    )
    assert result.outcome == "transfer_rejected"
    assert "Referred-By" in result.reason


def test_bridge_survives_every_rooftop() -> None:
    for agent_id, target in (
        ("bdc-overflow", "queue:ford-service"),
        ("honda-trunk", "ext:2300"),
        ("service-did", "ext:1210"),
    ):
        result = attempt_transfer(
            GROUP,
            TransferAttempt(
                agent=agent_id,
                method="bridge",
                target=target,
                call=CallRequest(to_number=MAIN),
            ),
        )
        assert result.accepted, f"{agent_id} -> {target}: {result.reason}"
        assert result.recoverable


def test_bridge_to_a_bare_extension_needs_a_did_from_outside() -> None:
    """The agent on a hosted DID is outside the dial plan."""
    reachable = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="service-did",
            method="bridge",
            target="ext:1210",
            call=CallRequest(to_number=SERVICE),
        ),
    )
    unreachable = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="service-did",
            method="bridge",
            target="ext:1300",
            call=CallRequest(to_number=SERVICE),
        ),
    )
    assert reachable.accepted, "1210 has a DID"
    assert not unreachable.accepted, "1300 has no DID and is not dialable from a trunk"
    assert "no" in unreachable.reason.lower()


def test_blind_transfer_cannot_recover_but_bridge_can() -> None:
    call = CallRequest(
        to_number=HONDA_MAIN,
        occupied_channels={"tie-ford-honda": 4},
    )
    blind = attempt_transfer(
        GROUP,
        TransferAttempt(agent="honda-trunk", method="blind_refer", target="ext:2201", call=call),
    )
    bridged = attempt_transfer(
        GROUP,
        TransferAttempt(agent="honda-trunk", method="bridge", target="ext:2201", call=call),
    )
    assert blind.outcome == "caller_stranded"
    assert bridged.outcome == "recovered_by_agent"


def test_inband_dtmf_over_a_compressed_codec_loses_digits() -> None:
    result = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="used-lot",
            method="dtmf_redial",
            target="ext:3200",
            call=CallRequest(to_number=USED_MAIN),
        ),
    )
    assert result.outcome == "digits_lost"
    assert result.target_result is not None


def test_302_requires_an_unanswered_call() -> None:
    after = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="honda-trunk",
            method="sip_302",
            target="ext:2300",
            call=CallRequest(to_number=HONDA_MAIN),
            answered=True,
        ),
    )
    before = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="honda-trunk",
            method="sip_302",
            target="ext:2300",
            call=CallRequest(to_number=HONDA_MAIN),
            answered=False,
        ),
    )
    assert after.outcome == "transfer_rejected"
    assert before.accepted


def test_external_transfer_is_blocked_by_class_of_restriction() -> None:
    result = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="honda-trunk",
            method="blind_refer",
            target="external:+14155550901",
            call=CallRequest(to_number=HONDA_MAIN),
        ),
    )
    assert result.outcome == "transfer_rejected"
    assert "trunk-to-trunk" in result.reason


def test_replaced_group_uses_its_own_index():
    assert GROUP.extension("1200") is not None  # populate the original cache
    changed = replace(
        GROUP,
        extensions=tuple(
            replace(station, name="Replacement advisor") if station.number == "1200" else station
            for station in GROUP.extensions
        ),
    )
    assert changed.extension("1200").name == "Replacement advisor"
    assert GROUP.extension("1200").name != "Replacement advisor"


def test_group_validation_reports_all_dangling_references_before_routing():
    assert validate_group(GROUP) == ()
    changed = replace(
        GROUP,
        dids=(*GROUP.dids, replace(GROUP.dids[0], target="ext:9999")),
        time_conditions=tuple(
            replace(condition, schedule="missing-schedule")
            if condition.id == "ford-main-hours"
            else condition
            for condition in GROUP.time_conditions
        ),
    )
    errors = validate_group(changed)
    assert any("duplicate did" in error for error in errors)
    assert any("9999" in error for error in errors)
    assert any("missing-schedule" in error for error in errors)
    with pytest.raises(ValueError, match="Invalid dealer group"):
        route(changed, CallRequest(to_number=MAIN))


def test_overnight_hours_continue_into_next_day_and_holidays_win():
    schedule = Schedule(
        "night", "Night staff", {"fri": (22 * 60, 2 * 60)}, holidays=("2026-09-12",)
    )
    assert schedule.state_at("fri", minutes("22:00")) == "open"
    assert schedule.state_at("sat", minutes("01:59")) == "open"
    assert schedule.state_at("sat", minutes("02:00")) == "closed"
    assert schedule.state_at("sat", minutes("01:00"), "2026-09-12") == "holiday"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"time": "25:00"},
        {"time": "10:99"},
        {"day": "monday"},
        {"patience_seconds": -1},
        {"occupied_channels": {"used-pri-1": -1}},
        {"presence": {"1200": "missing"}},
    ],
)
def test_invalid_call_requests_fail_at_the_boundary(kwargs):
    with pytest.raises(ValueError):
        CallRequest(to_number=MAIN, **kwargs)


def test_ivr_invalid_entry_uses_configured_destination():
    changed = replace(
        GROUP,
        ivr_menus=tuple(
            replace(menu, invalid_target="vm:ford-service") if menu.id == "ford-main" else menu
            for menu in GROUP.ivr_menus
        ),
    )
    result = route(changed, CallRequest(to_number=MAIN, digits=("9",)))
    assert "vm:ford-service" in result.path
    assert "attendant:ford" not in result.path


def test_offnet_forward_counts_outbound_channel_and_honors_direction():
    changed = replace(
        GROUP,
        dids=tuple(
            replace(did, target=f"external:{GROUP.answering_service}")
            if did.number == USED_MAIN
            else did
            for did in GROUP.dids
        ),
    )
    result = route(changed, CallRequest(to_number=USED_MAIN))
    assert result.outcome == "answering_service"
    assert result.cdr["channel_usage"] == {"used-pri-1": 2}
    full = route(changed, CallRequest(to_number=USED_MAIN, occupied_channels={"used-pri-1": 7}))
    assert full.outcome == "congestion"
    inbound_only = replace(
        changed,
        trunks=tuple(
            replace(trunk, direction="in") if trunk.id == "used-pri-1" else trunk
            for trunk in changed.trunks
        ),
    )
    assert route(inbound_only, CallRequest(to_number=USED_MAIN)).outcome == "congestion"


def test_bridge_from_outside_uses_the_station_did_and_carrier_capacity():
    call = CallRequest(to_number=SERVICE, occupied_channels={"ford-pri-1": 23, "ford-sip-1": 20})
    result = attempt_transfer(
        GROUP, TransferAttempt(agent="service-did", method="bridge", target="ext:1210", call=call)
    )
    assert result.outcome == "recovered_by_agent"
    assert result.target_result.outcome == "congestion"


def test_transfer_limit_and_border_replaces_policy_are_enforced():
    exhausted = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="honda-trunk",
            method="bridge",
            target="ext:2300",
            call=CallRequest(to_number=HONDA_MAIN),
            previous_transfers=3,
        ),
    )
    assert exhausted.outcome == "transfer_rejected"
    assert "limit" in exhausted.reason.lower()
    changed = replace(
        GROUP,
        border_elements=tuple(
            replace(border, allows_replaces=False) for border in GROUP.border_elements
        ),
    )
    result = attempt_transfer(
        changed,
        TransferAttempt(
            agent="honda-trunk",
            method="attended_refer",
            target="ext:2300",
            call=CallRequest(to_number=HONDA_MAIN),
        ),
    )
    assert result.outcome == "transfer_rejected"
    assert transfer_matrix(changed)["honda"]["attended_refer"] is False


def test_transfer_completion_occurs_after_the_target_leg():
    result = attempt_transfer(
        GROUP,
        TransferAttempt(
            agent="honda-trunk",
            method="bridge",
            target="ext:2300",
            call=CallRequest(to_number=HONDA_MAIN),
        ),
    )
    assert result.events[-1].at >= result.events[1].at + result.target_result.duration_seconds


def test_queue_abandonment_reports_the_actual_queue_limit():
    result = route(
        GROUP, CallRequest(to_number=SERVICE, presence=ALL_SERVICE_BUSY, patience_seconds=5)
    )
    event = next(event for event in result.events if event.kind == "abandon")
    assert "5s of a 210s hold" in event.detail


@pytest.mark.parametrize("channels", [-1, 1.5, True])
def test_capacity_rejects_invalid_channel_counts(channels):
    with pytest.raises(ValueError, match="nonnegative integer"):
        capacity_report(GROUP, "used", channels=channels)


@pytest.mark.parametrize("target", ["tie:honda/2300", "external:+14155550901"])
def test_restricted_station_cannot_forward_outside_its_allowed_scope(target):
    group = replace(
        GROUP,
        coverage_paths=(
            *GROUP.coverage_paths,
            CoveragePath("restricted", "Restricted path", (CoveragePoint("all", target),)),
        ),
        extensions=tuple(
            replace(station, cor="cor-restricted", coverage="restricted")
            if station.number == "1200"
            else station
            for station in GROUP.extensions
        ),
    )
    result = route(group, CallRequest(to_number=MAIN, digits=("1200",), presence={"1200": "busy"}))
    assert result.outcome == "rejected"
    assert "restricted" in result.events[-1].label.lower()
