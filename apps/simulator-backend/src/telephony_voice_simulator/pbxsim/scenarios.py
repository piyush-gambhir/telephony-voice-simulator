"""Scenario pack for the dealer-group PBX model.

Each case is a question about the phone system with a checked answer. Running the
suite is a regression test over the routing model, and reading the cases is the
fastest way to learn what the model can express.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dealer_group import default_group
from .model import DealerGroup
from .router import CallRequest, route
from .transfer import TransferAttempt, TransferResult, attempt_transfer


@dataclass(frozen=True)
class PbxCase:
    id: str
    title: str
    teaches: str
    request: CallRequest
    expected_outcome: str
    transfer: TransferAttempt | None = None
    expected_transfer: str | None = None


_MAIN = "+14155550100"
_SERVICE = "+14155550102"
_PARTS = "+14155550103"
_GM = "+14155550110"
_HONDA_MAIN = "+14155550200"
_HONDA_SERVICE = "+14155550202"
_USED_MAIN = "+14155550300"
_BDC = "+14155550400"
_AGENT_DID = "+14155550777"

_ALL_BDC_BUSY = {"1900": "busy", "1901": "busy", "1902": "busy", "1120": "busy"}
_ALL_SERVICE_BUSY = {"1200": "busy", "1201": "busy", "1202": "busy", "1220": "busy"}


def _call(to: str, **kwargs: Any) -> CallRequest:
    return CallRequest(to_number=to, **kwargs)


CASES: tuple[PbxCase, ...] = (
    # --------------------------------------------------------- inbound routing #
    PbxCase(
        "service-direct-open",
        "Service DID during service hours",
        "A direct service number skips the attendant and drops straight into the queue. "
        "The advisors are all tied up, so the cashier takes a technical question.",
        _call(_SERVICE, day="tue", time="10:30", intent="is my car ready?"),
        "answered_human",
    ),
    PbxCase(
        "service-overflow-to-agent",
        "Service queue overflows to the voice agent",
        "Every advisor and every BDC seat is busy, so the queue's overflow reaches the "
        "agent registered as extension 1999. This is the intended production path.",
        _call(
            _SERVICE,
            day="tue",
            time="10:30",
            intent="need a loaner",
            presence={**_ALL_SERVICE_BUSY, **_ALL_BDC_BUSY},
        ),
        "answered_agent",
    ),
    PbxCase(
        "main-line-sales",
        "Main line, caller presses 1 for sales",
        "Standard attendant routing into a circular hunt group on the showroom floor.",
        _call(_MAIN, day="tue", time="10:30", digits=("1",), intent="new F-150 pricing"),
        "answered_human",
    ),
    PbxCase(
        "dial-by-extension-local",
        "Caller dials a known extension at the attendant",
        "Dial-by-extension bypasses the menu entirely, which is how repeat customers "
        "and other dealers reach a specific person.",
        _call(_MAIN, day="tue", time="10:30", digits=("1210",), intent="warranty escalation"),
        "answered_human",
    ),
    PbxCase(
        "dial-by-extension-other-rooftop",
        "Caller dials a Honda extension from the Ford attendant",
        "The uniform dial plan resolves the extension to another rooftop and the call "
        "crosses the E&M tie trunk, then falls through Honda coverage back to the group "
        "BDC over the same tie trunk in the other direction.",
        _call(_MAIN, day="tue", time="10:30", digits=("2200",), intent="Honda service"),
        "answered_human",
    ),
    PbxCase(
        "parts-linear-hunt",
        "Parts direct number",
        "A linear hunt group always presents the counter phone first, which is why the "
        "wholesale desk never picks up until the counter is busy.",
        _call(_PARTS, day="tue", time="10:30", intent="brake pads for a 2019 Escape"),
        "answered_human",
    ),
    PbxCase(
        "invalid-entries-fall-to-operator",
        "Caller presses invalid digits twice",
        "Retry budget then timeout target. The operator absorbs every confused caller.",
        _call(_MAIN, day="tue", time="10:30", digits=("7", "8"), intent="unsure"),
        "answered_human",
    ),
    PbxCase(
        "gm-direct-offline-to-mailbox",
        "GM direct DID while the station is unregistered",
        "An unregistered station is treated as busy, not as no-answer, so the coverage "
        "path skips the assistant and goes straight to the mailbox.",
        _call(_GM, day="tue", time="14:00", intent="escalation"),
        "voicemail",
    ),
    # ------------------------------------------------------------ time of day #
    PbxCase(
        "after-hours-service-mailbox-full",
        "Service line after hours with a full mailbox",
        "The single most expensive silent failure in a dealership: after-hours service "
        "calls route to a mailbox that has been full for weeks and nobody is told.",
        _call(_SERVICE, day="tue", time="20:30", intent="car broke down"),
        "mailbox_full",
    ),
    PbxCase(
        "sunday-service-closed",
        "Honda service on a Sunday",
        "Service is closed Sunday while sales is open, so the two departments answer "
        "the same caller differently on the same day.",
        _call(_HONDA_SERVICE, day="sun", time="11:00", intent="oil change booking"),
        "voicemail",
    ),
    PbxCase(
        "holiday-dead-end",
        "Main line on Christmas Day",
        "The holiday branch plays an announcement and hangs up. There is no message "
        "path at all, so every holiday lead is lost without a trace.",
        _call(_MAIN, day="thu", time="11:00", date="12-25", intent="holiday sale"),
        "no_answer",
    ),
    PbxCase(
        "used-lot-after-hours-answering-service",
        "Used lot after hours",
        "The used lot forwards off-net to a paid answering service, which burns two "
        "channels and leaves no recording the dealer can review.",
        _call(_USED_MAIN, day="tue", time="21:30", intent="is the Civic still available?"),
        "answering_service",
    ),
    # ------------------------------------------------------------- capacity   #
    PbxCase(
        "used-lot-pri-congestion",
        "Weekend sale saturates the used lot PRI",
        "Eight B-channels is the hard ceiling on concurrent calls. Callers get fast "
        "busy and never appear in any report, because congested calls make no CDR.",
        _call(
            _USED_MAIN,
            day="sat",
            time="11:00",
            occupied_channels={"used-pri-1": 8},
            intent="sale event",
        ),
        "congestion",
    ),
    PbxCase(
        "bdc-caller-abandons",
        "Caller gives up in the BDC queue",
        "Patience is shorter than the queue's maximum wait, so the lead is lost with no "
        "CRM activity created at all.",
        _call(
            _BDC,
            day="tue",
            time="10:30",
            presence=_ALL_BDC_BUSY,
            patience_seconds=45,
            intent="trade-in appraisal",
        ),
        "abandoned",
    ),
    PbxCase(
        "unassigned-number",
        "Caller dials a number the group does not own",
        "Guards the DID inventory: a number that is not provisioned never reaches a "
        "rooftop, which is what a mis-typed tracking number looks like.",
        _call("+14155559999", intent="old printed number"),
        "unassigned_number",
    ),
    # ---------------------------------------------------------- tie trunk lore #
    PbxCase(
        "tandem-through-ford",
        "Honda caller asks for a used-lot salesperson",
        "There is no Honda-to-Used tie trunk, so the call tandems through Ford and "
        "holds a channel on both tie trunks for its whole duration.",
        _call(_HONDA_MAIN, day="tue", time="10:30", digits=("3100",), intent="used inventory"),
        "answered_human",
    ),
    PbxCase(
        "glare-misroutes-to-attendant",
        "Glare on the immediate-start tie trunk",
        "Two ends seize the same analog channel at once. Immediate start has already "
        "outpulsed the digits, so they are lost and the call lands on the far-end "
        "attendant instead of the service advisor the caller asked for.",
        _call(
            _USED_MAIN,
            day="tue",
            time="10:30",
            digits=("2",),
            glare_trunks=("tie-ford-used",),
            intent="service on a used purchase",
        ),
        "answered_human",
    ),
    PbxCase(
        "tie-trunk-congestion",
        "Both Ford-to-Used tie channels are busy",
        "Two channels is all there is between the stores. Intersite routing fails long "
        "before the carrier trunks do.",
        _call(
            _USED_MAIN,
            day="tue",
            time="10:30",
            digits=("2",),
            occupied_channels={"tie-ford-used": 2},
            intent="service question",
        ),
        "congestion",
    ),
    # --------------------------------------------------------------- the agent #
    PbxCase(
        "agent-answers-forwarded-call",
        "Ford service line forwards to the agent's own DID",
        "Outside the dial plan the agent depends entirely on the Diversion header to "
        "know which rooftop and department the caller wanted.",
        _call(_AGENT_DID, day="tue", time="10:30", intent="service appointment"),
        "answered_agent",
    ),
    PbxCase(
        "forwarded-agent-exhausts-the-last-channel",
        "Forwarding to the agent fails for want of a second channel",
        "The inbound leg gets the last free channel, then the switch has nothing left "
        "to re-originate towards the agent's outside number. Forwarding needs two "
        "channels per call, so a line-constrained store loses the call at half the "
        "capacity it thinks it has.",
        _call(
            "+14155550788",
            day="tue",
            time="10:30",
            occupied_channels={"used-pri-1": 7},
            intent="after-hours enquiry during a busy period",
        ),
        "congestion",
    ),
    PbxCase(
        "registered-agent-costs-one-channel",
        "A registered agent takes the same call on one channel",
        "Identical pressure, but the agent is a station inside the dial plan, so the "
        "only carrier channel involved is the inbound leg. This is the entire argument "
        "for peering over forwarding at a site with limited lines.",
        _call(
            _SERVICE,
            day="tue",
            time="10:30",
            occupied_channels={"ford-pri-1": 22},
            presence={**_ALL_SERVICE_BUSY, **_ALL_BDC_BUSY},
            intent="service enquiry at peak",
        ),
        "answered_agent",
    ),
    PbxCase(
        "agent-blind-refer-rejected-at-ford",
        "Agent tries a blind transfer at Ford",
        "The hardened SBC rejects REFER outright. No amount of correct SIP on the "
        "agent side changes this; the rooftop simply cannot accept a blind transfer.",
        _call(_SERVICE, day="tue", time="10:30", presence={**_ALL_SERVICE_BUSY, **_ALL_BDC_BUSY}),
        "answered_agent",
        TransferAttempt(
            agent="bdc-overflow",
            method="blind_refer",
            target="ext:1200",
            call=_call(_SERVICE, day="tue", time="10:30"),
        ),
        "transfer_rejected",
    ),
    PbxCase(
        "agent-bridge-works-at-ford",
        "Agent bridges instead of transferring",
        "Bridging needs no switch feature, so it works where REFER cannot. The caller "
        "reaches the service queue and the agent stays in the path.",
        _call(_SERVICE, day="tue", time="10:30", presence={**_ALL_SERVICE_BUSY, **_ALL_BDC_BUSY}),
        "answered_agent",
        TransferAttempt(
            agent="bdc-overflow",
            method="bridge",
            target="queue:ford-service",
            call=_call(_SERVICE, day="tue", time="10:30"),
        ),
        "transferred_answered",
    ),
    PbxCase(
        "agent-bridge-lands-in-voicemail",
        "Agent bridges to an unattended station",
        "The warranty administrator is with a customer, so a caller who was promised a "
        "person gets a mailbox. Presence has to be checked before committing.",
        _call(_SERVICE, day="tue", time="10:30", presence={**_ALL_SERVICE_BUSY, **_ALL_BDC_BUSY}),
        "answered_agent",
        TransferAttempt(
            agent="bdc-overflow",
            method="bridge",
            target="ext:1230",
            call=_call(_SERVICE, day="tue", time="10:30", presence={"1230": "with_customer"}),
        ),
        "transferred_voicemail",
    ),
    PbxCase(
        "agent-302-after-answer-rejected",
        "Agent tries a 302 redirect after answering",
        "Redirect is a call-setup mechanism. Once the agent has spoken to the caller "
        "the dialog is established and 302 is illegal.",
        _call(_AGENT_DID, day="tue", time="10:30"),
        "answered_agent",
        TransferAttempt(
            agent="service-did",
            method="sip_302",
            target="ext:1210",
            call=_call(_SERVICE, day="tue", time="10:30"),
            answered=True,
        ),
        "transfer_rejected",
    ),
    PbxCase(
        "agent-dtmf-redial-at-ford",
        "Agent re-dials the main number and sends the extension",
        "The fallback for an agent with no SIP relationship: a second PSTN leg, the "
        "attendant greeting, then digits. It works, but the caller waits in dead air.",
        _call(_AGENT_DID, day="tue", time="10:30"),
        "answered_agent",
        TransferAttempt(
            agent="service-did",
            method="dtmf_redial",
            target="ext:1200",
            call=_call(_MAIN, day="tue", time="10:30"),
        ),
        "transferred_answered",
    ),
    PbxCase(
        "agent-dtmf-digits-lost-at-used-lot",
        "DTMF re-dial on the used lot",
        "Inband DTMF over G.729 destroys the digits. The attendant hears nothing and "
        "the caller falls to the timeout target instead of the service writer.",
        _call("+14155550788", day="tue", time="10:30"),
        "answered_agent",
        TransferAttempt(
            agent="used-lot",
            method="dtmf_redial",
            target="ext:3200",
            call=_call(_USED_MAIN, day="tue", time="10:30"),
        ),
        "digits_lost",
    ),
    PbxCase(
        "agent-refer-accepted-at-honda",
        "Agent blind-transfers at Honda",
        "CUBE consumes the REFER and completes the transfer itself. It works, but the "
        "agent gets no NOTIFY and therefore never learns where the caller ended up.",
        _call(_HONDA_MAIN, day="tue", time="10:30", digits=("1",)),
        "answered_human",
        TransferAttempt(
            agent="honda-trunk",
            method="blind_refer",
            target="ext:2700",
            call=_call(_HONDA_MAIN, day="tue", time="10:30"),
        ),
        "transferred_voicemail",
    ),
    PbxCase(
        "agent-external-transfer-blocked",
        "Agent tries to hand a caller to an off-net number",
        "Class of restriction blocks trunk-to-trunk transfer on every rooftop in this "
        "group, so no agent can pass a caller to a mobile or the answering service.",
        _call(_HONDA_MAIN, day="tue", time="10:30", digits=("1",)),
        "answered_human",
        TransferAttempt(
            agent="honda-trunk",
            method="blind_refer",
            target="external:+14155550901",
            call=_call(_HONDA_MAIN, day="tue", time="10:30"),
        ),
        "transfer_rejected",
    ),
    PbxCase(
        "agent-blind-transfer-strands-caller",
        "Blind transfer while the tie trunk is full",
        "The transfer is accepted, the agent releases the call, and only then does the "
        "tie trunk congest. Nobody is holding the caller when it fails.",
        _call(_HONDA_MAIN, day="tue", time="10:30", digits=("1",)),
        "answered_human",
        TransferAttempt(
            agent="honda-trunk",
            method="blind_refer",
            target="ext:2201",
            call=_call(
                _HONDA_MAIN,
                day="tue",
                time="10:30",
                occupied_channels={"tie-ford-honda": 4},
            ),
        ),
        "caller_stranded",
    ),
)


def get_case(case_id: str) -> PbxCase | None:
    return next((case for case in CASES if case.id == case_id), None)


def run_case(case: PbxCase, group: DealerGroup | None = None) -> dict[str, Any]:
    group = group or default_group()
    call_result = route(group, case.request)
    transfer_result: TransferResult | None = None
    if case.transfer is not None:
        transfer_result = attempt_transfer(group, case.transfer)

    passed = call_result.outcome == case.expected_outcome
    if case.expected_transfer is not None:
        passed = passed and (
            transfer_result is not None and transfer_result.outcome == case.expected_transfer
        )
    return {
        "id": case.id,
        "title": case.title,
        "teaches": case.teaches,
        "expected_outcome": case.expected_outcome,
        "actual_outcome": call_result.outcome,
        "expected_transfer": case.expected_transfer,
        "actual_transfer": transfer_result.outcome if transfer_result else None,
        "passed": passed,
        "call": call_result.as_dict(),
        "transfer": transfer_result.as_dict() if transfer_result else None,
    }


def run_suite(group: DealerGroup | None = None) -> list[dict[str, Any]]:
    group = group or default_group()
    return [run_case(case, group) for case in CASES]
