"""Channel cost of each voice-agent integration pattern.

For a dealership with a small trunk group, how a voice agent is attached is not
only a feature question. It decides how many of the site's carrier channels a
single agent-handled call occupies, and therefore how many calls the store can
take at once before callers hear fast busy.

The arithmetic is simple and it is the whole argument:

* An agent reached by **forwarding to an outside number** is not handed the call.
  The switch keeps the inbound leg up and re-originates a second leg across its
  own trunk group. Two channels, for the call's whole duration. Handing the caller
  back means a third.
* An agent reached over a **private SIP trunk**, or registered as a station, is
  inside the dial plan. The inbound leg is the only carrier channel involved, and
  a handoff back to a station adds none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import DealerGroup, IngressMode
from .validation import require_valid_group


@dataclass(frozen=True)
class ChannelCost:
    ingress: IngressMode
    label: str
    inbound_channels: int
    handoff_channels: int
    inside_dial_plan: bool
    explanation: str

    @property
    def worst_case(self) -> int:
        return self.inbound_channels + self.handoff_channels


#: ``handoff_channels`` assumes a bridged handoff, the only mechanism that works
#: on every switch. A successful REFER releases the agent's leg and costs one
#: fewer channel, but REFER is rejected outright on most hardened deployments.
COSTS: tuple[ChannelCost, ...] = (
    ChannelCost(
        "carrier_routed",
        "Carrier routes the number straight to the agent",
        inbound_channels=0,
        handoff_channels=1,
        inside_dial_plan=False,
        explanation=(
            "The dealership's switch is never involved on the way in, so the call costs "
            "none of its channels and there is no forwarding leg to rewrite the caller ID. "
            "Handing a caller back still means dialling in from outside. Works no matter "
            "how old the switch is, because it does not touch it."
        ),
    ),
    ChannelCost(
        "forwarded_with_diversion",
        "Call forwarding to the agent's outside number",
        inbound_channels=2,
        handoff_channels=1,
        inside_dial_plan=False,
        explanation=(
            "The switch holds the inbound leg and re-originates a second leg over the "
            "same trunk group. Handing the caller back means a third leg, because the "
            "agent has to dial in from the public network like any other outsider."
        ),
    ),
    ChannelCost(
        "pstn_owned_did",
        "Agent publishes its own number, dealership forwards or advertises it",
        inbound_channels=2,
        handoff_channels=1,
        inside_dial_plan=False,
        explanation=(
            "Identical channel cost to plain forwarding. The only difference is whose "
            "number is printed on the marketing."
        ),
    ),
    ChannelCost(
        "sip_trunk_peer",
        "Private SIP trunk between the dealership's SBC and the agent",
        inbound_channels=1,
        handoff_channels=0,
        inside_dial_plan=True,
        explanation=(
            "The leg to the agent rides the private trunk, not the carrier trunk, so it "
            "costs no carrier channel. Handing back to a station stays inside the switch."
        ),
    ),
    ChannelCost(
        "registered_extension",
        "Agent registered as a station on the dealership's switch",
        inbound_channels=1,
        handoff_channels=0,
        inside_dial_plan=True,
        explanation=(
            "The agent is a station like any desk phone. Same channel cost as routing to "
            "a human advisor, which is to say none beyond the inbound leg."
        ),
    ),
    ChannelCost(
        "queue_overflow",
        "Agent as a queue overflow target inside the dial plan",
        inbound_channels=1,
        handoff_channels=0,
        inside_dial_plan=True,
        explanation=(
            "A dial-plan destination like any other. Requires the agent to already be "
            "peered or registered."
        ),
    ),
)


def cost_for(ingress: IngressMode) -> ChannelCost:
    match = next((item for item in COSTS if item.ingress == ingress), None)
    if match is None:
        raise ValueError(f"Unknown ingress mode '{ingress}'")
    return match


def carrier_channels(group: DealerGroup, site_id: str) -> int:
    """Total carrier channels at a site, excluding private tie trunks."""
    return sum(trunk.channels for trunk in group.inbound_trunks(site_id))


def capacity_report(
    group: DealerGroup,
    site_id: str,
    *,
    channels: int | None = None,
) -> dict[str, Any]:
    """Concurrent agent-handled calls a site supports under each ingress pattern.

    ``channels`` overrides the modelled trunk size, so a real dealership's line
    count can be dropped in without editing the group config.
    """
    require_valid_group(group)
    site = group.site(site_id)
    if site is None:
        raise ValueError(f"Unknown site '{site_id}'")
    total = channels if channels is not None else carrier_channels(group, site_id)
    if type(total) is not int or total < 0:
        raise ValueError("carrier channels must be a nonnegative integer")

    rows = []
    for cost in COSTS:
        steady = total // cost.inbound_channels if cost.inbound_channels else None
        with_handoff = total // cost.worst_case if cost.worst_case else None
        rows.append(
            {
                "ingress": cost.ingress,
                "label": cost.label,
                "inside_dial_plan": cost.inside_dial_plan,
                "channels_per_call": cost.inbound_channels,
                "channels_per_call_with_handoff": cost.worst_case,
                "concurrent_agent_calls": steady,
                "concurrent_agent_calls_with_handoff": with_handoff,
                "limited_by": "dealership trunk" if cost.inbound_channels else "agent platform",
                "explanation": cost.explanation,
            }
        )

    forwarded = cost_for("forwarded_with_diversion")
    peered = cost_for("sip_trunk_peer")
    return {
        "site": site.label,
        "platform": site.platform,
        "carrier_channels": total,
        "rows": rows,
        "headline": {
            "forwarding_concurrent_calls": total // forwarded.inbound_channels,
            "peering_concurrent_calls": total // peered.inbound_channels,
            "capacity_multiplier": round(forwarded.inbound_channels / peered.inbound_channels, 2),
            "forwarding_concurrent_calls_with_handoff": total // forwarded.worst_case,
            "handoff_multiplier": round(forwarded.worst_case / max(peered.worst_case, 1), 2),
        },
    }


def compare_sites(group: DealerGroup, channels: int | None = None) -> list[dict[str, Any]]:
    return [capacity_report(group, site.id, channels=channels) for site in group.sites]
