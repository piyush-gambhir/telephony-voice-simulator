"""Declarative model for a legacy on-prem dealer-group PBX.

The vocabulary here is deliberately the vocabulary a dealer group's telecom vendor
uses: trunks, class of restriction, coverage paths, hunt groups, night service,
tie trunks, and attendant consoles. The router in ``router.py`` walks this data;
nothing in this module executes a call.

Destinations are string references so the config reads like a dial plan:

``ext:1200``  ``hunt:ford-service-advisors``  ``queue:ford-service``
``ivr:ford-main``  ``time:ford-main-hours``  ``vm:1200``  ``attendant:ford``
``tie:honda/2200``  ``external:+14155550900``  ``agent:bdc-overflow``
``busy``  ``hangup``
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: Presence of a station. ``with_customer`` is the dealership-specific one: the
#: advisor is physically at a car with a customer, so the phone rings out rather
#: than returning busy. It is the single most common reason a dealer call fails.
PresenceState = Literal[
    "available",
    "busy",
    "with_customer",
    "offline",
    "dnd",
]

ScheduleState = Literal["open", "closed", "holiday"]

#: Terminal states a call can reach. Everything the router does ends in one of these.
CallOutcome = Literal[
    "answered_human",
    "answered_agent",
    "voicemail",
    "mailbox_full",
    "answering_service",
    "busy",
    "congestion",
    "no_answer",
    "abandoned",
    "unassigned_number",
    "loop_detected",
    "hop_limit",
    "rejected",
]

TrunkKind = Literal["pri", "sip", "tie_em", "analog"]

#: ``em_wink_start`` and ``em_immediate_start`` are the analog tie-trunk handshakes.
#: Wink start makes the far end confirm its digit receiver is ready; immediate start
#: blasts digits after a fixed guard time and loses them if the far end is slow.
Signaling = Literal["q931", "sip", "em_wink_start", "em_immediate_start"]

DtmfMode = Literal["rfc2833", "sip_info", "inband", "q931_keypad"]

#: How the border element treats an inbound REFER from an external party.
#: ``reject`` is the common hardened-SBC default and is why blind transfer fails.
#: ``consume`` means the SBC performs the transfer itself as a B2BUA.
ReferHandling = Literal["reject", "passthrough", "consume"]

CoverageTrigger = Literal["busy", "no_answer", "all"]

HuntType = Literal["linear", "circular", "ucd", "ddc", "simultaneous"]

#: How a voice agent is wired into the group. This decides which transfer methods
#: are even theoretically available to the agent later.
IngressMode = Literal[
    "carrier_routed",
    "pstn_owned_did",
    "forwarded_with_diversion",
    "sip_trunk_peer",
    "registered_extension",
    "queue_overflow",
]


def minutes(value: str) -> int:
    """``"07:30"`` -> ``450``."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{2}", value):
        raise ValueError("time must use HH:MM format")
    hour, _, minute = value.partition(":")
    if not 0 <= int(hour) < 24 or not 0 <= int(minute) < 60:
        raise ValueError("time must be between 00:00 and 23:59")
    return int(hour) * 60 + int(minute)


def clock(value: int) -> str:
    """``450`` -> ``"07:30"``."""
    return f"{value // 60:02d}:{value % 60:02d}"


@dataclass(frozen=True)
class Schedule:
    """Business hours plus fixed-date holidays.

    ``hours`` maps a weekday to ``(open_minute, close_minute)`` or ``None`` for a
    closed day. Dealer groups run different schedules per department, which is why
    a site owns several of these rather than one.
    """

    id: str
    label: str
    hours: dict[Weekday, tuple[int, int] | None]
    holidays: tuple[str, ...] = ()

    def state_at(self, day: Weekday, minute: int, date: str | None = None) -> ScheduleState:
        if day not in WEEKDAYS or not 0 <= minute < 1440:
            raise ValueError("schedule requires a valid weekday and minute of day")
        if date and date in self.holidays:
            return "holiday"
        previous_day = WEEKDAYS[(WEEKDAYS.index(day) - 1) % len(WEEKDAYS)]
        previous = self.hours.get(previous_day)
        if previous and previous[0] > previous[1] and minute < previous[1]:
            return "open"
        window = self.hours.get(day)
        if window is None:
            return "closed"
        start, end = window
        if start > end:
            return "open" if minute >= start else "closed"
        return "open" if start <= minute < end else "closed"


@dataclass(frozen=True)
class TimeCondition:
    """Night service in Avaya terms, a time condition in every other vendor's."""

    id: str
    label: str
    schedule: str
    open_target: str
    closed_target: str
    holiday_target: str | None = None


@dataclass(frozen=True)
class Trunk:
    """A path off the switch: carrier trunk, or tie trunk to a sister rooftop."""

    id: str
    label: str
    site: str
    kind: TrunkKind
    signaling: Signaling
    channels: int
    dtmf_mode: DtmfMode
    direction: Literal["in", "out", "both"] = "both"
    peer_site: str | None = None
    #: Analog tie trunks with no directional assignment collide when both ends
    #: seize at once. Modeled so the collision can actually be tested.
    glare_prone: bool = False
    #: Guard time before digits may be sent. Immediate-start trunks use a fixed
    #: value and lose digits if the far end is not ready; wink start waits for the
    #: far end to answer, so it is slower but safe.
    digit_guard_ms: int = 70


@dataclass(frozen=True)
class BorderElement:
    """SBC / CUBE / carrier gateway sitting in front of the switch."""

    id: str
    label: str
    refer_handling: ReferHandling
    allows_replaces: bool
    media_mode: Literal["relay", "passthrough"]
    normalizes_dtmf_to: DtmfMode | None = None
    #: Whether the border element will route ``sip:<extension>@site`` from an
    #: external peer. Almost always false on hardened legacy deployments.
    routes_bare_extensions: bool = False
    #: Rejects a transfer whose ``Referred-By`` is absent or untrusted.
    requires_referred_by: bool = True


@dataclass(frozen=True)
class ClassOfRestriction:
    """Avaya COR / Cisco CSS / NEC toll restriction, collapsed to what matters here."""

    id: str
    label: str
    allow_external_transfer: bool
    allow_trunk_to_trunk: bool
    allow_intersite: bool


@dataclass(frozen=True)
class CoveragePoint:
    when: CoverageTrigger
    target: str
    rings: int = 4


@dataclass(frozen=True)
class CoveragePath:
    """Ordered redirection points for an unanswered or busy station."""

    id: str
    label: str
    points: tuple[CoveragePoint, ...]


@dataclass(frozen=True)
class Extension:
    number: str
    name: str
    role: str
    site: str
    department: str
    device: str
    cor: str
    state: PresenceState = "available"
    coverage: str | None = None
    did: str | None = None
    #: Lines that also appear on this station (Avaya bridged appearance). A call
    #: to the principal alerts here too, which is how dealer GM/assistant pairs work.
    bridged_appearances: tuple[str, ...] = ()


@dataclass(frozen=True)
class HuntGroup:
    id: str
    label: str
    site: str
    type: HuntType
    members: tuple[str, ...]
    ring_seconds: int = 24
    overflow: str = "hangup"


@dataclass(frozen=True)
class Queue:
    """ACD queue. Legacy sites often bolt this on as a separate box."""

    id: str
    label: str
    site: str
    members: tuple[str, ...]
    strategy: HuntType = "ucd"
    sla_seconds: int = 45
    max_wait_seconds: int = 240
    overflow: str = "hangup"
    announce_position: bool = False


@dataclass(frozen=True)
class IvrMenu:
    id: str
    label: str
    site: str
    greeting: str
    options: dict[str, str]
    timeout_seconds: int = 7
    timeout_target: str = "attendant"
    invalid_target: str = "repeat"
    max_retries: int = 2
    allow_dial_by_extension: bool = False
    extension_length: int = 4
    greeting_seconds: int = 12


@dataclass(frozen=True)
class Mailbox:
    id: str
    label: str
    site: str
    #: A full mailbox is a real and frequent dealership condition.
    full: bool = False
    greeting_seconds: int = 14
    notifies: str | None = None


@dataclass(frozen=True)
class TransferPolicy:
    """What a third party is permitted to do to a call at this site."""

    refer_supported: bool
    refer_replaces: bool
    trunk_to_trunk_transfer: bool
    extensions_dialable_from_trunk: bool
    dtmf_relay: DtmfMode
    codec: Literal["g711u", "g711a", "g729", "opus"]
    attendant_transfer_available: bool = True
    max_transfer_hops: int = 2


@dataclass(frozen=True)
class Site:
    """One rooftop."""

    id: str
    label: str
    brand: str
    platform: str
    border_element: str | None
    main_did: str
    numbering_prefix: str
    attendant: str
    trunks: tuple[str, ...]
    schedules: tuple[str, ...]
    transfer_policy: TransferPolicy
    night_target: str
    notes: str = ""


@dataclass(frozen=True)
class Did:
    number: str
    label: str
    site: str
    target: str
    #: Marketing/tracking numbers are everywhere in automotive and matter because
    #: they change the attribution attached to the call, not the routing.
    tracking_source: str | None = None


@dataclass(frozen=True)
class AgentIntegration:
    """How one voice agent is attached to the group."""

    id: str
    label: str
    site: str
    ingress: IngressMode
    #: Extension number when registered, DID when we own the number.
    address: str
    #: Where the agent is reached from in the dial plan, if anywhere.
    reached_via: str
    receives_diversion: bool = False
    notes: str = ""


@dataclass(frozen=True)
class DealerGroup:
    id: str
    label: str
    sites: tuple[Site, ...]
    schedules: tuple[Schedule, ...]
    time_conditions: tuple[TimeCondition, ...]
    trunks: tuple[Trunk, ...]
    border_elements: tuple[BorderElement, ...]
    cors: tuple[ClassOfRestriction, ...]
    coverage_paths: tuple[CoveragePath, ...]
    extensions: tuple[Extension, ...]
    hunt_groups: tuple[HuntGroup, ...]
    queues: tuple[Queue, ...]
    ivr_menus: tuple[IvrMenu, ...]
    mailboxes: tuple[Mailbox, ...]
    dids: tuple[Did, ...]
    agents: tuple[AgentIntegration, ...]
    intersite_prefix: str = "8"
    answering_service: str = ""
    _index: dict[str, dict[str, object]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def _lookup(self, bucket: str, key: str) -> object | None:
        if not self._index:
            self._index.update(
                {
                    "site": {item.id: item for item in self.sites},
                    "schedule": {item.id: item for item in self.schedules},
                    "time": {item.id: item for item in self.time_conditions},
                    "trunk": {item.id: item for item in self.trunks},
                    "border": {item.id: item for item in self.border_elements},
                    "cor": {item.id: item for item in self.cors},
                    "coverage": {item.id: item for item in self.coverage_paths},
                    "ext": {item.number: item for item in self.extensions},
                    "hunt": {item.id: item for item in self.hunt_groups},
                    "queue": {item.id: item for item in self.queues},
                    "ivr": {item.id: item for item in self.ivr_menus},
                    "mailbox": {item.id: item for item in self.mailboxes},
                    "did": {item.number: item for item in self.dids},
                    "agent": {item.id: item for item in self.agents},
                }
            )
        return self._index[bucket].get(key)

    def site(self, site_id: str) -> Site | None:
        return self._lookup("site", site_id)  # type: ignore[return-value]

    def schedule(self, schedule_id: str) -> Schedule | None:
        return self._lookup("schedule", schedule_id)  # type: ignore[return-value]

    def time_condition(self, condition_id: str) -> TimeCondition | None:
        return self._lookup("time", condition_id)  # type: ignore[return-value]

    def trunk(self, trunk_id: str) -> Trunk | None:
        return self._lookup("trunk", trunk_id)  # type: ignore[return-value]

    def border_element(self, border_id: str | None) -> BorderElement | None:
        if border_id is None:
            return None
        return self._lookup("border", border_id)  # type: ignore[return-value]

    def cor(self, cor_id: str) -> ClassOfRestriction | None:
        return self._lookup("cor", cor_id)  # type: ignore[return-value]

    def coverage_path(self, path_id: str | None) -> CoveragePath | None:
        if path_id is None:
            return None
        return self._lookup("coverage", path_id)  # type: ignore[return-value]

    def extension(self, number: str) -> Extension | None:
        return self._lookup("ext", number)  # type: ignore[return-value]

    def hunt_group(self, hunt_id: str) -> HuntGroup | None:
        return self._lookup("hunt", hunt_id)  # type: ignore[return-value]

    def queue(self, queue_id: str) -> Queue | None:
        return self._lookup("queue", queue_id)  # type: ignore[return-value]

    def ivr_menu(self, menu_id: str) -> IvrMenu | None:
        return self._lookup("ivr", menu_id)  # type: ignore[return-value]

    def mailbox(self, mailbox_id: str) -> Mailbox | None:
        return self._lookup("mailbox", mailbox_id)  # type: ignore[return-value]

    def did(self, number: str) -> Did | None:
        return self._lookup("did", number)  # type: ignore[return-value]

    def agent(self, agent_id: str) -> AgentIntegration | None:
        return self._lookup("agent", agent_id)  # type: ignore[return-value]

    def site_extensions(self, site_id: str) -> tuple[Extension, ...]:
        return tuple(item for item in self.extensions if item.site == site_id)

    def inbound_trunks(self, site_id: str) -> tuple[Trunk, ...]:
        return tuple(
            item
            for item in self.trunks
            if item.site == site_id and item.kind != "tie_em" and item.direction != "out"
        )

    def outbound_trunks(self, site_id: str) -> tuple[Trunk, ...]:
        return tuple(
            item
            for item in self.trunks
            if item.site == site_id and item.kind != "tie_em" and item.direction != "in"
        )

    def tie_trunk(self, from_site: str, to_site: str) -> Trunk | None:
        for item in self.trunks:
            if item.kind != "tie_em":
                continue
            pair = {item.site, item.peer_site}
            if pair == {from_site, to_site}:
                return item
        return None
