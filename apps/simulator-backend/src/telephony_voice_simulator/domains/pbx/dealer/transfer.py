"""Agent-initiated transfer into the dealer group.

This is the half that decides whether a voice agent is deployable at a rooftop.
Answering a call is easy; handing the caller to the right human on a legacy
switch behind a hardened border element is where integrations die.

Each method is gated by the site's :class:`~.model.TransferPolicy` and border
element before the target leg is routed. The gate results are the point: they
tell you *in advance* which transfer method a given rooftop can actually use.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .model import AgentIntegration, DealerGroup, Extension, Site
from .router import CallEvent, CallRequest, CallResult, route, route_internal
from .validation import require_valid_group

TransferMethod = Literal[
    "blind_refer",
    "attended_refer",
    "bridge",
    "dtmf_redial",
    "sip_302",
]

TransferOutcome = Literal[
    "transferred_answered",
    "transferred_voicemail",
    "transfer_rejected",
    "caller_stranded",
    "recovered_by_agent",
    "digits_lost",
]


@dataclass
class TransferAttempt:
    """One handoff the agent tries to perform on a live call."""

    agent: str
    method: TransferMethod
    target: str
    call: CallRequest
    #: False models a redirect before the agent has answered the caller.
    answered: bool = True
    #: Agents that omit Referred-By are rejected by most hardened SBCs.
    send_referred_by: bool = True
    #: Announce the caller to the human before completing (attended only).
    consult_first: bool = True
    #: Transfers already performed on this call, before this attempt.
    previous_transfers: int = 0

    def __post_init__(self) -> None:
        if self.method not in {"blind_refer", "attended_refer", "bridge", "dtmf_redial", "sip_302"}:
            raise ValueError(f"Unknown transfer method '{self.method}'")
        if type(self.previous_transfers) is not int or self.previous_transfers < 0:
            raise ValueError("previous_transfers must be a nonnegative integer")


@dataclass
class TransferResult:
    outcome: TransferOutcome
    accepted: bool
    reason: str
    method: TransferMethod
    target: str
    recoverable: bool
    events: tuple[CallEvent, ...]
    notes: tuple[str, ...]
    target_result: CallResult | None = None
    alternatives: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "accepted": self.accepted,
            "reason": self.reason,
            "method": self.method,
            "target": self.target,
            "recoverable": self.recoverable,
            "notes": list(self.notes),
            "alternatives": list(self.alternatives),
            "timeline": [event.as_dict(index) for index, event in enumerate(self.events)],
            "target_leg": self.target_result.as_dict() if self.target_result else None,
        }


class _Builder:
    def __init__(self, start: int = 0) -> None:
        self.clock = start
        self.events: list[CallEvent] = []
        self.notes: list[str] = []

    def event(
        self,
        actor: str,
        kind: str,
        label: str,
        detail: str,
        ref: str = "",
        outcome: str = "in_progress",
        **metadata: Any,
    ) -> None:
        self.events.append(
            CallEvent(self.clock, actor, kind, label, detail, ref, outcome, metadata)
        )

    def advance(self, seconds: int) -> None:
        self.clock += seconds

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _target_station(group: DealerGroup, target: str) -> Extension | None:
    kind, _, value = target.partition(":")
    return group.extension(value) if kind == "ext" else None


def _is_internal(target: str) -> bool:
    return target.split(":", 1)[0] in {
        "ext",
        "hunt",
        "queue",
        "attendant",
        "vm",
        "ivr",
        "time",
        "tie",
    }


def _inside_dial_plan(agent: AgentIntegration) -> bool:
    """Whether the agent can address bare extensions at all."""
    return agent.ingress in {"registered_extension", "sip_trunk_peer", "queue_overflow"}


def _reject(
    builder: _Builder,
    attempt: TransferAttempt,
    reason: str,
    alternatives: tuple[str, ...],
) -> TransferResult:
    builder.event(
        "pbx",
        "transfer_rejected",
        "Transfer rejected",
        reason,
        attempt.target,
        "transfer_rejected",
        method=attempt.method,
    )
    builder.note(reason)
    return TransferResult(
        outcome="transfer_rejected",
        accepted=False,
        reason=reason,
        method=attempt.method,
        target=attempt.target,
        recoverable=True,
        events=tuple(builder.events),
        notes=tuple(builder.notes),
        alternatives=alternatives,
    )


def _alternatives(group: DealerGroup, site: Site, target: str, agent: AgentIntegration) -> tuple:
    """What the agent could do instead, given this rooftop's constraints."""
    options: list[str] = []
    station = _target_station(group, target)
    if station is not None and station.did:
        options.append(f"bridge a second leg to the station DID {station.did}")
    if _inside_dial_plan(agent) or not _is_internal(target):
        options.append("bridge a routable second leg and stay in the media path")
    if site.transfer_policy.attendant_transfer_available:
        options.append(f"bridge to {site.main_did} and hand off to the attendant verbally")
    if site.transfer_policy.dtmf_relay != "inband" or site.transfer_policy.codec.startswith("g711"):
        options.append(f"bridge to {site.main_did} and send the extension as DTMF")
    if not _inside_dial_plan(agent):
        options.append(
            "move this rooftop to a registered-extension or SIP-peer ingress so bare "
            "extensions become addressable"
        )
    return tuple(options)


def attempt_transfer(group: DealerGroup, attempt: TransferAttempt) -> TransferResult:
    """Evaluate one transfer against the target rooftop, then route the second leg."""
    require_valid_group(group)
    agent = group.agent(attempt.agent)
    if agent is None:
        raise ValueError(f"Unknown agent integration '{attempt.agent}'")
    site = group.site(agent.site)
    assert site is not None
    policy = site.transfer_policy
    border = group.border_element(site.border_element)
    builder = _Builder()

    builder.event(
        "agent",
        "transfer_request",
        f"Agent attempts {attempt.method} to {attempt.target}",
        f"{agent.label} is on the call via {agent.ingress} at {site.label} ({site.platform}).",
        attempt.target,
        method=attempt.method,
        ingress=agent.ingress,
    )

    if attempt.previous_transfers >= policy.max_transfer_hops:
        return _reject(
            builder,
            attempt,
            f"Transfer limit of {policy.max_transfer_hops} reached for this call.",
            (),
        )

    handlers = {
        "sip_302": _gate_302,
        "blind_refer": _gate_refer,
        "attended_refer": _gate_refer,
        "bridge": _gate_bridge,
        "dtmf_redial": _gate_dtmf,
    }
    gate = handlers[attempt.method]
    rejection = gate(group, site, agent, attempt, builder, border)
    if rejection is not None:
        return rejection

    recoverable = attempt.method in {"bridge", "attended_refer"}
    if attempt.method == "attended_refer" and border is not None:
        if border.refer_handling == "consume":
            recoverable = False
            builder.note(
                f"{border.label} consumes the REFER and performs the transfer itself, so the "
                "agent receives no NOTIFY progress and cannot take the caller back."
            )

    target_result = _route_target(group, site, agent, attempt, builder)
    builder.advance(target_result.duration_seconds)
    return _classify(attempt, builder, target_result, recoverable)


# ------------------------------------------------------------------- gates --- #


def _gate_302(group, site, agent, attempt, builder, border) -> TransferResult | None:
    if attempt.answered:
        return _reject(
            builder,
            attempt,
            "302 Moved Temporarily is only valid before the agent answers. The agent has "
            "already answered, so the dialog is established and a redirect is illegal.",
            _alternatives(group, site, attempt.target, agent),
        )
    if agent.ingress not in {"sip_trunk_peer"}:
        return _reject(
            builder,
            attempt,
            "The carrier leg ignores 302 on inbound calls. Redirect only works when the "
            "agent is peered directly with the customer's border element.",
            _alternatives(group, site, attempt.target, agent),
        )
    builder.advance(1)
    builder.event(
        "agent",
        "redirect",
        "302 Moved Temporarily",
        f"The agent redirects the unanswered INVITE to {attempt.target}.",
        attempt.target,
    )
    return None


def _gate_refer(group, site, agent, attempt, builder, border) -> TransferResult | None:
    policy = site.transfer_policy
    alternatives = _alternatives(group, site, attempt.target, agent)

    if not policy.refer_supported or (border is not None and border.refer_handling == "reject"):
        label = border.label if border else site.platform
        return _reject(
            builder,
            attempt,
            f"{label} rejects REFER from an external party (405 Method Not Allowed). "
            "Blind transfer is not available at this rooftop at all.",
            alternatives,
        )

    if attempt.method == "attended_refer" and (
        not policy.refer_replaces or (border is not None and not border.allows_replaces)
    ):
        return _reject(
            builder,
            attempt,
            "The switch does not support REFER with Replaces, so a consultative transfer "
            "cannot splice the two legs together.",
            alternatives,
        )

    if border is not None and border.requires_referred_by and not attempt.send_referred_by:
        return _reject(
            builder,
            attempt,
            f"{border.label} returns 403 Forbidden because the REFER carries no trusted "
            "Referred-By header.",
            alternatives,
        )

    if _is_internal(attempt.target):
        addressable = policy.extensions_dialable_from_trunk or _inside_dial_plan(agent)
        if not addressable:
            station = _target_station(group, attempt.target)
            hint = (
                f" Station {station.number} does have a DID ({station.did}), so a bridged "
                "leg to that number would reach it."
                if station is not None and station.did
                else ""
            )
            return _reject(
                builder,
                attempt,
                f"{attempt.target} is a private dial-plan address. The border element will "
                "not route a bare extension from an external peer, so Refer-To has no "
                "reachable URI." + hint,
                alternatives,
            )
    elif not policy.trunk_to_trunk_transfer:
        return _reject(
            builder,
            attempt,
            "Class of restriction blocks trunk-to-trunk transfer, so the switch will not "
            "connect an inbound trunk call to an outbound trunk. This is the single most "
            "common reason an external transfer fails on a legacy dealer PBX.",
            alternatives,
        )

    builder.advance(2)
    if border is not None and border.refer_handling == "consume":
        builder.event(
            "sbc",
            "refer",
            f"{border.label} consumes the REFER",
            "The border element accepts the REFER and places the new call itself as a "
            "B2BUA. The agent is released immediately and sees no NOTIFY progress.",
            attempt.target,
            refer_handling="consume",
        )
        builder.note(
            "Transfer succeeds but is invisible: no NOTIFY sipfrag reaches the agent, so a "
            "failed handoff cannot be detected, let alone recovered."
        )
    else:
        builder.event(
            "pbx",
            "refer",
            "REFER accepted",
            f"The switch accepts REFER to {attempt.target} and returns 202 Accepted, "
            "then NOTIFY sipfrag as the new leg progresses.",
            attempt.target,
            refer_handling="passthrough",
        )
    if attempt.method == "attended_refer" and attempt.consult_first:
        builder.advance(8)
        builder.event(
            "agent",
            "consult",
            "Agent consults the target first",
            "The caller is placed on hold while the agent announces the call.",
            attempt.target,
        )
    return None


def _gate_bridge(group, site, agent, attempt, builder, border) -> TransferResult | None:
    if _is_internal(attempt.target) and not _inside_dial_plan(agent):
        station = _target_station(group, attempt.target)
        if station is None or not station.did:
            return _reject(
                builder,
                attempt,
                f"A bridged leg has to dial a routable number, and {attempt.target} has no "
                "DID. From outside the dial plan this station can only be reached through "
                f"the main number {site.main_did}.",
                _alternatives(group, site, attempt.target, agent),
            )
        builder.note(f"Bridging to the station DID {station.did} rather than the bare extension.")

    builder.advance(3)
    builder.event(
        "agent",
        "bridge",
        "Agent bridges a second leg",
        "The agent stays in the media path as a B2BUA and conferences the two legs. No "
        "switch transfer feature is required; the target must be routable and have capacity.",
        attempt.target,
    )
    builder.note(
        "Bridging holds two channels and keeps the agent in the media path for the whole "
        "call. It is the only method that survives every policy here, and the only one "
        "that lets the agent take the caller back when the target does not answer."
    )
    return None


def _gate_dtmf(group, site, agent, attempt, builder, border) -> TransferResult | None:
    policy = site.transfer_policy
    station = _target_station(group, attempt.target)
    if station is None:
        return _reject(
            builder,
            attempt,
            "DTMF re-dial can only target a station reachable from the auto attendant by "
            "extension digits.",
            _alternatives(group, site, attempt.target, agent),
        )

    menu = next(
        (item for item in group.ivr_menus if item.site == site.id and item.allow_dial_by_extension),
        None,
    )
    if menu is None:
        return _reject(
            builder,
            attempt,
            f"No auto attendant at {site.label} accepts dial-by-extension, so there is no "
            "digit path to the station.",
            _alternatives(group, site, attempt.target, agent),
        )

    builder.advance(4)
    builder.event(
        "agent",
        "redial",
        f"Agent dials {site.main_did} on a second leg",
        "The agent places a fresh PSTN call to the main number and waits for the attendant "
        "greeting before sending digits.",
        site.main_did,
        dtmf_relay=policy.dtmf_relay,
        codec=policy.codec,
    )

    if policy.dtmf_relay == "inband" and not policy.codec.startswith("g711"):
        builder.advance(6)
        builder.event(
            "agent",
            "digits_lost",
            "Extension digits destroyed in transit",
            f"Inband DTMF over {policy.codec} does not survive the codec. The attendant "
            "never registers the digits and the call falls to the timeout target.",
            attempt.target,
            "digits_lost",
        )
        builder.note(
            f"{site.label} carries inband DTMF over {policy.codec}. Any digit-driven "
            "handoff is unreliable here; negotiate G.711 or RFC 2833 before relying on it."
        )
        fallback = replace(
            attempt.call,
            to_number=site.main_did,
            digits=(),
            intent="agent re-dial with lost digits",
        )
        result = route(group, fallback)
        return TransferResult(
            outcome="digits_lost",
            accepted=False,
            reason="Inband DTMF over a compressed codec destroyed the extension digits.",
            method=attempt.method,
            target=attempt.target,
            recoverable=True,
            events=tuple(builder.events),
            notes=tuple(builder.notes),
            target_result=result,
            alternatives=_alternatives(group, site, attempt.target, agent),
        )

    builder.advance(14)
    builder.event(
        "agent",
        "dtmf",
        f"Agent sends {station.number} to the attendant",
        f"Digits are relayed as {policy.dtmf_relay} after the greeting has started.",
        attempt.target,
        digits=station.number,
    )
    builder.note(
        "DTMF re-dial adds a second PSTN leg and roughly 20 seconds of dead air for the "
        "caller before the target even rings."
    )
    return None


# ------------------------------------------------------------- second leg --- #


def _route_target(
    group: DealerGroup,
    site: Site,
    agent: AgentIntegration,
    attempt: TransferAttempt,
    builder: _Builder,
) -> CallResult:
    base = replace(
        attempt.call,
        intent=f"transferred by {agent.label}",
    )
    if attempt.method == "dtmf_redial":
        station = _target_station(group, attempt.target)
        assert station is not None
        return route(group, replace(base, to_number=site.main_did, digits=(station.number,)))
    if attempt.method == "bridge" and not _inside_dial_plan(agent):
        station = _target_station(group, attempt.target)
        if station is not None and station.did:
            return route(group, replace(base, to_number=station.did, digits=()))
    return route_internal(group, site.id, attempt.target, base)


def _classify(
    attempt: TransferAttempt,
    builder: _Builder,
    target_result: CallResult,
    recoverable: bool,
) -> TransferResult:
    answered = target_result.outcome in {"answered_human", "answered_agent", "answering_service"}
    notes = list(builder.notes) + [
        note for note in target_result.notes if note not in builder.notes
    ]

    if answered:
        outcome: TransferOutcome = "transferred_answered"
        reason = f"The target answered ({target_result.answered_by_label})."
        builder.event(
            "pbx",
            "transfer_complete",
            "Transfer completed",
            reason,
            attempt.target,
            "transferred_answered",
        )
    elif target_result.outcome in {"voicemail"}:
        outcome = "transferred_voicemail"
        reason = "The target did not answer and the caller landed in voicemail."
        builder.event(
            "pbx",
            "transfer_voicemail",
            "Transfer landed in voicemail",
            "The caller was told they would reach a person and reached a mailbox instead.",
            attempt.target,
            "transferred_voicemail",
        )
        notes.append(
            "A blind handoff into voicemail is worse than no transfer: the caller was "
            "promised a human. Check the target's presence before committing."
        )
    elif recoverable:
        outcome = "recovered_by_agent"
        reason = (
            f"The target leg ended as {target_result.outcome}; the agent still holds the "
            "caller and can offer another option."
        )
        builder.event(
            "agent",
            "recover",
            "Agent takes the caller back",
            reason,
            attempt.target,
            "recovered_by_agent",
        )
    else:
        outcome = "caller_stranded"
        reason = (
            f"The target leg ended as {target_result.outcome} and the agent had already "
            "released the call, so nobody is holding the caller."
        )
        builder.event(
            "pbx",
            "stranded",
            "Caller stranded",
            reason,
            attempt.target,
            "caller_stranded",
        )
        notes.append(
            "Blind transfer gives up control before the outcome is known. On a rooftop "
            "where advisors are rarely at the desk, this loses real customers."
        )

    return TransferResult(
        outcome=outcome,
        accepted=True,
        reason=reason,
        method=attempt.method,
        target=attempt.target,
        recoverable=recoverable,
        events=tuple(builder.events),
        notes=tuple(notes),
        target_result=target_result,
    )


def transfer_matrix(group: DealerGroup) -> dict[str, dict[str, Any]]:
    """Which transfer methods each rooftop can support, before any call is placed."""
    require_valid_group(group)
    matrix: dict[str, dict[str, Any]] = {}
    for site in group.sites:
        policy = site.transfer_policy
        border = group.border_element(site.border_element)
        refer_ok = policy.refer_supported and (border is None or border.refer_handling != "reject")
        matrix[site.id] = {
            "site": site.label,
            "platform": site.platform,
            "border_element": border.label if border else None,
            "blind_refer": refer_ok,
            "attended_refer": refer_ok
            and policy.refer_replaces
            and (border is None or border.allows_replaces),
            "bridge": True,
            "dtmf_redial": (policy.dtmf_relay != "inband" or policy.codec.startswith("g711"))
            and any(
                menu.site == site.id and menu.allow_dial_by_extension for menu in group.ivr_menus
            ),
            "external_transfer": policy.trunk_to_trunk_transfer,
            "bare_extensions_from_trunk": policy.extensions_dialable_from_trunk,
            "notes": site.notes,
        }
    return matrix
