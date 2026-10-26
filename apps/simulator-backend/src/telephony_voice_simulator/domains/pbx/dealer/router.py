"""Deterministic call router for a dealer-group PBX.

Given a :class:`CallRequest` the router walks the declarative config in
``dealer_group.py`` and produces an outcome plus the timeline that got there.
Nothing is random: the same request always yields the same result, which is what
makes the model usable as a regression suite for a voice agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import (
    CallOutcome,
    CoveragePath,
    DealerGroup,
    Extension,
    HuntGroup,
    IvrMenu,
    PresenceState,
    Queue,
    Site,
    Trunk,
    Weekday,
    WEEKDAYS,
    minutes,
)
from .validation import require_valid_group

RING_CYCLE_SECONDS = 6
GREETING_ANSWER_SECONDS = 2
MAX_HOPS = 14


@dataclass(frozen=True)
class CallEvent:
    at: int
    actor: str
    kind: str
    label: str
    detail: str
    ref: str = ""
    outcome: str = "in_progress"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "at": f"{self.at // 60:02d}:{self.at % 60:02d}",
            "at_seconds": self.at,
            "actor": self.actor,
            "kind": self.kind,
            "label": self.label,
            "detail": self.detail,
            "ref": self.ref,
            "outcome": self.outcome,
            **self.metadata,
        }


@dataclass
class CallRequest:
    """One inbound call attempt.

    ``digits`` is the sequence the caller enters, consumed one menu level at a
    time. An entry may be a single option digit (``"2"``), a full extension
    (``"1200"``), or an intersite string (``"81200"``).
    """

    to_number: str
    from_number: str = "+14085550131"
    day: Weekday = "tue"
    time: str = "10:30"
    date: str | None = None
    digits: tuple[str, ...] = ()
    intent: str = ""
    caller_label: str = "Caller"
    #: Seconds the caller is willing to hold before hanging up.
    patience_seconds: int = 600
    #: Station presence overrides, keyed by extension number.
    presence: dict[str, PresenceState] = field(default_factory=dict)
    #: Occupied B-channels / sessions per trunk id, to force congestion.
    occupied_channels: dict[str, int] = field(default_factory=dict)
    #: Tie trunks that collide on seize for this call.
    glare_trunks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        minutes(self.time)
        if self.day not in WEEKDAYS:
            raise ValueError("day must be one of mon, tue, wed, thu, fri, sat, sun")
        if type(self.patience_seconds) is not int or self.patience_seconds < 0:
            raise ValueError("patience_seconds must be a nonnegative integer")
        if any(type(count) is not int or count < 0 for count in self.occupied_channels.values()):
            raise ValueError("occupied channels must be nonnegative integers")
        if any(
            state not in {"available", "busy", "with_customer", "offline", "dnd"}
            for state in self.presence.values()
        ):
            raise ValueError("presence contains an unknown station state")

    @property
    def minute(self) -> int:
        return minutes(self.time)


@dataclass
class CallResult:
    outcome: CallOutcome
    answered_by: str | None
    answered_by_label: str | None
    site: str | None
    path: tuple[str, ...]
    events: tuple[CallEvent, ...]
    duration_seconds: int
    notes: tuple[str, ...]
    cdr: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "answered_by": self.answered_by,
            "answered_by_label": self.answered_by_label,
            "site": self.site,
            "path": list(self.path),
            "duration_seconds": self.duration_seconds,
            "notes": list(self.notes),
            "cdr": self.cdr,
            "timeline": [event.as_dict(index) for index, event in enumerate(self.events)],
        }


class CallRouter:
    """Walks one call through the dealer group."""

    def __init__(self, group: DealerGroup, request: CallRequest) -> None:
        require_valid_group(group)
        self.group = group
        self.request = request
        self.clock = 0
        self.events: list[CallEvent] = []
        self.path: list[str] = []
        self.notes: list[str] = []
        self.hops = 0
        self.visited: list[str] = []
        self.current_site: str | None = None
        self.station_context: Extension | None = None
        self._digits = list(request.digits)
        self._trunks_used: list[str] = []
        self._answer_at: int | None = None
        #: Channels this call is itself holding, per trunk. A forwarded call holds
        #: two on the same carrier trunk group: the inbound leg and the leg the
        #: switch re-originates towards the forwarding target.
        self._channel_usage: dict[str, int] = {}
        self.carrier_channels_used = 0

    # ----------------------------------------------------------------- utils #

    def _event(
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

    def _advance(self, seconds: int) -> None:
        self.clock += seconds

    def _note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def _presence(self, extension: Extension) -> PresenceState:
        return self.request.presence.get(extension.number, extension.state)

    def _channel_free(self, trunk: Trunk) -> bool:
        occupied = self.request.occupied_channels.get(trunk.id, 0)
        return occupied + self._channel_usage.get(trunk.id, 0) < trunk.channels

    def _hold_channel(self, trunk: Trunk) -> None:
        self._channel_usage[trunk.id] = self._channel_usage.get(trunk.id, 0) + 1
        self._trunks_used.append(trunk.id)
        if trunk.kind != "tie_em":
            self.carrier_channels_used += 1

    def _finish(
        self,
        outcome: CallOutcome,
        answered_by: str | None = None,
        answered_by_label: str | None = None,
    ) -> CallResult:
        disposition = {
            "answered_human": "ANSWERED",
            "answered_agent": "ANSWERED",
            "voicemail": "VOICEMAIL",
            "mailbox_full": "FAILED",
            "answering_service": "ANSWERED",
            "busy": "BUSY",
            "congestion": "CONGESTION",
            "no_answer": "NO ANSWER",
            "abandoned": "ABANDONED",
            "unassigned_number": "FAILED",
            "loop_detected": "FAILED",
            "hop_limit": "FAILED",
            "rejected": "FAILED",
        }[outcome]
        cdr = {
            "calling_number": self.request.from_number,
            "called_number": self.request.to_number,
            "day": self.request.day,
            "start_time": self.request.time,
            "site": self.current_site,
            "trunks": list(dict.fromkeys(self._trunks_used)),
            "carrier_channels_used": self.carrier_channels_used,
            "channel_usage": dict(self._channel_usage),
            "hops": self.hops,
            "answer_seconds": self._answer_at,
            "duration_seconds": self.clock,
            "disposition": disposition,
            "answered_by": answered_by,
        }
        did = self.group.did(self.request.to_number)
        if did and did.tracking_source:
            cdr["tracking_source"] = did.tracking_source
        return CallResult(
            outcome=outcome,
            answered_by=answered_by,
            answered_by_label=answered_by_label,
            site=self.current_site,
            path=tuple(self.path),
            events=tuple(self.events),
            duration_seconds=self.clock,
            notes=tuple(self.notes),
            cdr=cdr,
        )

    # ------------------------------------------------------------------ entry #

    def run(self) -> CallResult:
        request = self.request
        self._event(
            "caller",
            "dial",
            "Caller dials",
            f"{request.from_number} dials {request.to_number}"
            + (f": {request.intent}" if request.intent else "."),
            request.to_number,
            caller=request.caller_label,
            day=request.day,
            time=request.time,
        )

        did = self.group.did(request.to_number)
        if did is None:
            self._event(
                "carrier",
                "reject",
                "Number not in DID inventory",
                "The carrier has no route for this number; the caller hears an intercept.",
                request.to_number,
                "unassigned_number",
            )
            self._note("Dialed number is not provisioned on any rooftop.")
            return self._finish("unassigned_number")

        site = self.group.site(did.site)
        assert site is not None
        self.current_site = site.id

        trunk_outcome = self._seize_inbound_trunk(site)
        if trunk_outcome is not None:
            return trunk_outcome

        if did.tracking_source:
            self._event(
                "crm",
                "attribution",
                "Tracking number matched",
                f"Call attributed to the {did.tracking_source} source before routing.",
                did.number,
                tracking_source=did.tracking_source,
            )

        self._event(
            "pbx",
            "inbound_route",
            "Inbound route applied",
            f"{site.platform} maps {did.number} ({did.label}) to {did.target}.",
            did.target,
            site=site.id,
        )
        return self.resolve(did.target)

    def _seize_inbound_trunk(self, site: Site) -> CallResult | None:
        trunks = self.group.inbound_trunks(site.id)
        if not trunks:
            self._note(f"No inbound trunk configured at {site.label}.")
            return self._finish("congestion")

        chosen = None
        for trunk in trunks:
            if self._channel_free(trunk):
                chosen = trunk
                break
            occupied = self.request.occupied_channels.get(trunk.id, 0)
            self._event(
                "carrier",
                "congestion",
                f"{trunk.label} is full",
                f"All {trunk.channels} channels are in use; the carrier tries the next trunk.",
                trunk.id,
                trunk_channels=trunk.channels,
                occupied=occupied,
            )

        if chosen is None:
            release = "Q.931 cause 34 no circuit available" if trunks[0].kind == "pri" else "503"
            self._event(
                "carrier",
                "congestion",
                "All trunks busy",
                f"The switch returns {release}; the caller hears fast busy.",
                site.id,
                "congestion",
            )
            self._note(
                f"{site.label} has no free channel. Trunk capacity is the ceiling on "
                "concurrent calls, including anything a voice agent originates."
            )
            return self._finish("congestion")

        self._hold_channel(chosen)
        if chosen.signaling == "q931":
            detail = "Carrier sends Q.931 SETUP with the called-party number on the D-channel."
        else:
            detail = "Carrier sends SIP INVITE; the border element answers 100 Trying."
        self._advance(1)
        self._event(
            "trunk",
            "seize",
            f"Inbound on {chosen.label}",
            detail,
            chosen.id,
            signaling=chosen.signaling,
            dtmf_mode=chosen.dtmf_mode,
        )

        border = self.group.border_element(site.border_element)
        if border is not None:
            self._event(
                "sbc",
                "normalize",
                f"{border.label}",
                "Border element normalises signalling and anchors media"
                + (
                    f"; DTMF is normalised to {border.normalizes_dtmf_to}."
                    if border.normalizes_dtmf_to
                    else "."
                ),
                border.id,
                refer_handling=border.refer_handling,
                media_mode=border.media_mode,
            )
        return None

    # -------------------------------------------------------------- resolution #

    def resolve(self, ref: str) -> CallResult:
        self.hops += 1
        if self.hops > MAX_HOPS:
            self._event(
                "pbx",
                "abort",
                "Hop limit reached",
                "The call was redirected too many times and the switch tears it down.",
                ref,
                "hop_limit",
            )
            self._note("Routing exceeded the hop limit; check for a redirect chain.")
            return self._finish("hop_limit")
        if ref in self.visited and not ref.startswith(("vm:", "announce:")):
            self._event(
                "pbx",
                "loop",
                "Routing loop detected",
                f"{ref} has already been visited on this call.",
                ref,
                "loop_detected",
            )
            self._note(f"Routing loop: {ref} reached twice.")
            return self._finish("loop_detected")

        self.visited.append(ref)
        self.path.append(ref)

        kind, _, value = ref.partition(":")
        handler = {
            "time": self._do_time,
            "ivr": self._do_ivr,
            "ext": self._do_extension,
            "hunt": self._do_hunt,
            "queue": self._do_queue,
            "vm": self._do_voicemail,
            "attendant": self._do_attendant,
            "tie": self._do_tie,
            "external": self._do_external,
            "agent": self._do_agent,
            "announce": self._do_announce,
        }.get(kind)

        if handler is not None:
            return handler(value)
        if ref == "busy":
            self._event("pbx", "busy", "Busy tone", "The switch returns busy.", ref, "busy")
            return self._finish("busy")
        if ref == "hangup":
            self._event("pbx", "hangup", "Call cleared", "The switch clears the call.", ref)
            return self._finish("no_answer")

        self._note(f"Unroutable destination '{ref}'.")
        self._event("pbx", "reject", "Unroutable destination", ref, ref, "rejected")
        return self._finish("rejected")

    # ---------------------------------------------------------------- handlers #

    def _do_time(self, condition_id: str) -> CallResult:
        condition = self.group.time_condition(condition_id)
        if condition is None:
            return self.resolve("hangup")
        schedule = self.group.schedule(condition.schedule)
        assert schedule is not None
        state = schedule.state_at(self.request.day, self.request.minute, self.request.date)
        target = {
            "open": condition.open_target,
            "closed": condition.closed_target,
            "holiday": condition.holiday_target or condition.closed_target,
        }[state]
        self._event(
            "pbx",
            "time_condition",
            f"{condition.label}: {state}",
            f"{schedule.label} evaluates to {state} for {self.request.day} "
            f"{self.request.time}; routing to {target}.",
            condition.id,
            schedule_state=state,
        )
        if state != "open":
            self._note(f"{condition.label} was {state} at {self.request.day} {self.request.time}.")
        return self.resolve(target)

    def _do_ivr(self, menu_id: str) -> CallResult:
        menu = self.group.ivr_menu(menu_id)
        if menu is None:
            return self.resolve("hangup")
        self.current_site = menu.site
        self._advance(GREETING_ANSWER_SECONDS)
        self._answer_at = self._answer_at or self.clock
        self._event(
            "ivr",
            "answer",
            f"{menu.label} answers",
            menu.greeting,
            f"ivr:{menu.id}",
            greeting_seconds=menu.greeting_seconds,
            options=sorted(menu.options),
        )

        attempts = 0
        while attempts <= menu.max_retries:
            entry = self._digits.pop(0) if self._digits else None
            if entry is None:
                self._advance(menu.greeting_seconds + menu.timeout_seconds)
                self._event(
                    "ivr",
                    "timeout",
                    "No digits collected",
                    f"The menu waits {menu.timeout_seconds}s and applies the timeout target.",
                    f"ivr:{menu.id}",
                )
                return self.resolve(menu.timeout_target)

            self._advance(menu.greeting_seconds // 2 + 2)
            resolved = self._menu_target(menu, entry)
            if resolved is not None:
                self._event(
                    "caller",
                    "dtmf",
                    f"Caller enters {entry}",
                    f"The attendant accepts {entry} and routes to {resolved}.",
                    f"ivr:{menu.id}",
                    digits=entry,
                )
                return self.resolve(resolved)

            attempts += 1
            self._event(
                "ivr",
                "invalid",
                f"Invalid entry {entry}",
                f"Attempt {attempts} of {menu.max_retries + 1}; the greeting repeats.",
                f"ivr:{menu.id}",
                digits=entry,
            )
            if menu.invalid_target != "repeat":
                return self.resolve(menu.invalid_target)

        self._note(f"Caller exhausted retries at {menu.label}.")
        return self.resolve(menu.timeout_target)

    def _menu_target(self, menu: IvrMenu, entry: str) -> str | None:
        if entry in menu.options:
            return menu.options[entry]
        if not menu.allow_dial_by_extension:
            return None
        prefix = self.group.intersite_prefix
        if entry.startswith(prefix) and len(entry) == menu.extension_length + len(prefix):
            target = entry[len(prefix) :]
            station = self.group.extension(target)
            if station is not None:
                if station.site == menu.site:
                    return f"ext:{target}"
                return f"tie:{station.site}/{target}"
            return None
        if len(entry) == menu.extension_length and self.group.extension(entry) is not None:
            station = self.group.extension(entry)
            assert station is not None
            if station.site != menu.site:
                return f"tie:{station.site}/{entry}"
            return f"ext:{entry}"
        return None

    def _do_extension(self, number: str) -> CallResult:
        station = self.group.extension(number)
        if station is None:
            self._note(f"Extension {number} is not in the directory.")
            self._event(
                "pbx",
                "reject",
                f"Unknown extension {number}",
                "The switch has no such station; the caller is intercepted.",
                f"ext:{number}",
                "rejected",
            )
            return self._finish("rejected")

        self.current_site = station.site
        self.station_context = station
        state = self._presence(station)
        coverage = self.group.coverage_path(station.coverage)

        if station.bridged_appearances:
            self._event(
                "pbx",
                "bridged",
                f"Bridged appearance on {station.name}",
                f"The call also alerts lines {', '.join(station.bridged_appearances)}.",
                f"ext:{station.number}",
                bridged=list(station.bridged_appearances),
            )

        if state == "available":
            self._advance(RING_CYCLE_SECONDS)
            self._event(
                "station",
                "ring",
                f"{station.name} x{station.number} rings",
                f"{station.device} alerts with caller ID and the called-party name.",
                f"ext:{station.number}",
                device=station.device,
                department=station.department,
            )
            self._advance(3)
            self._answer_at = self._answer_at or self.clock
            self._event(
                "station",
                "answer",
                f"{station.name} answers",
                "Media cuts through and the CDR records answer supervision.",
                f"ext:{station.number}",
                "answered_human",
                role=station.role,
            )
            return self._finish("answered_human", station.number, station.name)

        trigger = "busy" if state in {"busy", "dnd"} else "no_answer"
        if state == "offline":
            trigger = "busy"
            self._event(
                "station",
                "offline",
                f"{station.name} x{station.number} is unregistered",
                "The station is not registered; the switch redirects immediately.",
                f"ext:{station.number}",
                presence=state,
            )
        elif state in {"busy", "dnd"}:
            self._event(
                "station",
                "busy",
                f"{station.name} x{station.number} is {state}",
                "The station returns busy without alerting.",
                f"ext:{station.number}",
                presence=state,
            )
        else:
            rings = coverage.points[0].rings if coverage and coverage.points else 4
            self._advance(max(rings, 1) * RING_CYCLE_SECONDS)
            self._event(
                "station",
                "no_answer",
                f"{station.name} x{station.number} rings out",
                f"The advisor is with a customer; {rings} ring cycles go unanswered.",
                f"ext:{station.number}",
                presence=state,
                rings=rings,
            )
            self._note(
                f"{station.name} was {state.replace('_', ' ')}. Dealership stations are "
                "unattended far more often than they are busy."
            )

        target = self._coverage_target(coverage, trigger, station)
        if target is None:
            outcome: CallOutcome = "busy" if trigger == "busy" else "no_answer"
            self._event(
                "pbx",
                "clear",
                "No coverage path",
                "The station has no coverage point for this condition.",
                f"ext:{station.number}",
                outcome,
            )
            return self._finish(outcome)

        self._event(
            "pbx",
            "coverage",
            f"Coverage point on {trigger.replace('_', ' ')}",
            f"{coverage.label if coverage else 'Coverage'} redirects to {target}.",
            f"ext:{station.number}",
            trigger=trigger,
        )
        return self.resolve(target)

    def _coverage_target(
        self,
        coverage: CoveragePath | None,
        trigger: str,
        station: Extension,
    ) -> str | None:
        if coverage is None:
            return None
        for point in coverage.points:
            if point.when in {trigger, "all"}:
                if point.target == "vm:station":
                    return f"vm:station/{station.number}"
                return point.target
        return None

    def _members(self, members: tuple[str, ...], strategy: str) -> list[Extension]:
        stations = [
            station
            for station in (self.group.extension(number) for number in members)
            if station is not None
        ]
        if strategy in {"linear", "ddc", "simultaneous"}:
            return stations
        if strategy == "ucd":
            # Uniform call distribution: idle stations first, then the rest.
            return sorted(stations, key=lambda item: self._presence(item) != "available")
        if strategy == "circular":
            return stations
        return stations

    def _do_hunt(self, hunt_id: str) -> CallResult:
        hunt: HuntGroup | None = self.group.hunt_group(hunt_id)
        if hunt is None:
            return self.resolve("hangup")
        self.current_site = hunt.site
        self._event(
            "pbx",
            "hunt",
            f"{hunt.label} hunt group",
            f"{hunt.type} hunt across {len(hunt.members)} stations for up to {hunt.ring_seconds}s.",
            f"hunt:{hunt.id}",
            hunt_type=hunt.type,
            members=list(hunt.members),
        )
        stations = self._members(hunt.members, hunt.type)
        states = {station.number: self._presence(station) for station in stations}
        available = [station for station in stations if states[station.number] == "available"]

        if available:
            station = available[0]
            self._advance(RING_CYCLE_SECONDS)
            self._answer_at = self._answer_at or self.clock
            self._event(
                "station",
                "answer",
                f"{station.name} x{station.number} answers from {hunt.label}",
                "The hunt group stops on the first idle station.",
                f"ext:{station.number}",
                "answered_human",
                role=station.role,
            )
            return self._finish("answered_human", station.number, station.name)

        ringing = [
            station
            for station in stations
            if states[station.number] in {"with_customer", "available"}
        ]
        if ringing:
            self._advance(hunt.ring_seconds)
            self._event(
                "pbx",
                "no_answer",
                f"{hunt.label} rings out",
                f"{len(ringing)} station(s) alerted for {hunt.ring_seconds}s with no answer.",
                f"hunt:{hunt.id}",
            )
        else:
            self._advance(2)
            self._event(
                "pbx",
                "busy",
                f"{hunt.label} is all busy",
                "Every member is busy or unregistered; the group overflows immediately.",
                f"hunt:{hunt.id}",
            )
        self._note(f"{hunt.label} did not answer; overflow to {hunt.overflow}.")
        return self.resolve(hunt.overflow)

    def _do_queue(self, queue_id: str) -> CallResult:
        queue: Queue | None = self.group.queue(queue_id)
        if queue is None:
            return self.resolve("hangup")
        self.current_site = queue.site
        stations = self._members(queue.members, queue.strategy)
        available = [station for station in stations if self._presence(station) == "available"]
        self._event(
            "queue",
            "enter",
            f"{queue.label}",
            f"{queue.strategy} distribution, {queue.sla_seconds}s SLA, "
            f"{queue.max_wait_seconds}s maximum wait.",
            f"queue:{queue.id}",
            sla_seconds=queue.sla_seconds,
            members=list(queue.members),
            announce_position=queue.announce_position,
        )

        if available:
            wait = min(queue.sla_seconds, 12)
            if self.request.patience_seconds < wait:
                return self._abandon(wait)
            self._advance(wait)
            station = available[0]
            self._answer_at = self._answer_at or self.clock
            self._event(
                "station",
                "answer",
                f"{station.name} x{station.number} takes the queued call",
                f"Answered {wait}s into the queue, inside the {queue.sla_seconds}s SLA.",
                f"ext:{station.number}",
                "answered_human",
                queue_wait=wait,
            )
            return self._finish("answered_human", station.number, station.name)

        wait = queue.max_wait_seconds
        if self.request.patience_seconds < wait:
            return self._abandon(wait)
        self._advance(wait)
        self._event(
            "queue",
            "overflow",
            f"{queue.label} maximum wait reached",
            f"No agent became available in {wait}s; the queue applies {queue.overflow}.",
            f"queue:{queue.id}",
            queue_wait=wait,
        )
        self._note(
            f"{queue.label} overflowed after {wait}s with every member busy. This is the "
            "gap a voice agent is bought to cover."
        )
        return self.resolve(queue.overflow)

    def _abandon(self, wait: int) -> CallResult:
        self._advance(self.request.patience_seconds)
        self._event(
            "caller",
            "abandon",
            "Caller hangs up on hold",
            f"The caller waited {self.request.patience_seconds}s of a {wait}s hold and left.",
            "queue",
            "abandoned",
        )
        self._note("Abandoned in queue: the lead is lost and no CRM activity is created.")
        return self._finish("abandoned")

    def _do_voicemail(self, value: str) -> CallResult:
        box_id, _, station_number = value.partition("/")
        mailbox = self.group.mailbox(box_id)
        label = mailbox.label if mailbox else box_id
        if station_number:
            station = self.group.extension(station_number)
            if station is not None:
                label = f"{station.name} x{station.number} mailbox"
        if mailbox is None:
            self._note(f"Mailbox '{box_id}' is not configured.")
            return self._finish("no_answer")

        self._advance(3)
        if mailbox.full:
            self._event(
                "voicemail",
                "full",
                f"{label} is full",
                "The system plays a mailbox-full prompt and disconnects the caller.",
                f"vm:{box_id}",
                "mailbox_full",
            )
            self._note(
                f"{label} is full. A full service mailbox silently drops every "
                "after-hours lead and nobody at the store finds out."
            )
            return self._finish("mailbox_full")

        self._answer_at = self._answer_at or self.clock
        self._advance(mailbox.greeting_seconds)
        self._event(
            "voicemail",
            "record",
            f"{label} records a message",
            "The caller hears the greeting and leaves a message.",
            f"vm:{box_id}",
            "voicemail",
            notifies=mailbox.notifies,
        )
        if mailbox.notifies:
            self._event(
                "crm",
                "activity",
                "Voicemail notification",
                f"A callback task is queued in {mailbox.notifies}.",
                f"vm:{box_id}",
                "callback_queued",
            )
        return self._finish("voicemail", box_id, label)

    def _do_attendant(self, site_id: str) -> CallResult:
        site = self.group.site(site_id)
        if site is None:
            return self.resolve("hangup")
        self.current_site = site.id
        self._event(
            "pbx",
            "attendant",
            f"Attendant console at {site.label}",
            "The call is presented to the operator position.",
            f"attendant:{site.id}",
        )
        return self.resolve(f"ext:{site.attendant}")

    def _do_tie(self, value: str) -> CallResult:
        site_id, _, target = value.partition("/")
        from_site = self.current_site or site_id
        destination = self._tie_destination(site_id, target)

        if from_site == site_id:
            return self.resolve(destination)
        if self.station_context is not None:
            restriction = self.group.cor(self.station_context.cor)
            if restriction is not None and not restriction.allow_intersite:
                self._event(
                    "pbx",
                    "reject",
                    "Intersite routing restricted",
                    f"{restriction.label} blocks this station from routing to another site.",
                    value,
                    "rejected",
                )
                return self._finish("rejected")

        trunk = self.group.tie_trunk(from_site, site_id)
        if trunk is None:
            tandem = self._tandem_site(from_site, site_id)
            if tandem is None:
                self._note(f"No tie path from {from_site} to {site_id}.")
                self._event(
                    "pbx",
                    "reject",
                    "No tie trunk",
                    f"{from_site} has no route to {site_id} and no tandem point.",
                    value,
                    "rejected",
                )
                return self._finish("rejected")
            self._event(
                "pbx",
                "tandem",
                f"Tandem through {tandem}",
                f"{from_site} has no direct tie to {site_id}; the call tandems via {tandem}, "
                "consuming a channel on both tie trunks for the whole call.",
                value,
                tandem_site=tandem,
            )
            first = self.group.tie_trunk(from_site, tandem)
            second = self.group.tie_trunk(tandem, site_id)
            for leg in (first, second):
                if leg is None:
                    return self._finish("rejected")
                failure = self._seize_tie(leg, site_id)
                if failure is not None:
                    return failure
            self.current_site = site_id
            return self.resolve(destination)

        failure = self._seize_tie(trunk, site_id)
        if failure is not None:
            return failure
        self.current_site = site_id
        return self.resolve(destination)

    def _tie_destination(self, site_id: str, target: str) -> str:
        if target.isdigit():
            return f"ext:{target}"
        if self.group.queue(target) is not None:
            return f"queue:{target}"
        if self.group.hunt_group(target) is not None:
            return f"hunt:{target}"
        site = self.group.site(site_id)
        return f"ext:{site.attendant}" if site else "hangup"

    def _tandem_site(self, from_site: str, to_site: str) -> str | None:
        for site in self.group.sites:
            if site.id in {from_site, to_site}:
                continue
            if self.group.tie_trunk(from_site, site.id) and self.group.tie_trunk(site.id, to_site):
                return site.id
        return None

    def _seize_tie(self, trunk: Trunk, target_site: str) -> CallResult | None:
        if not self._channel_free(trunk):
            self._event(
                "trunk",
                "congestion",
                f"{trunk.label} is full",
                f"All {trunk.channels} tie channels are busy; the caller hears reorder.",
                trunk.id,
                "congestion",
            )
            self._note(
                f"{trunk.label} only has {trunk.channels} channels. Intersite transfers "
                "fail first when the group runs a sales event."
            )
            return self._finish("congestion")

        self._hold_channel(trunk)
        if trunk.signaling == "em_wink_start":
            self._advance(2)
            detail = (
                "The near end raises its M lead to seize; the far end returns a wink on the "
                f"E lead after {trunk.digit_guard_ms}ms to confirm its register is ready, "
                "then digits are outpulsed."
            )
        else:
            self._advance(1)
            detail = (
                f"The near end seizes and outpulses digits after a fixed {trunk.digit_guard_ms}ms "
                "guard time without waiting for the far end to confirm."
            )
        self._event(
            "trunk",
            "tie_seize",
            f"Seize {trunk.label}",
            detail,
            trunk.id,
            signaling=trunk.signaling,
            target_site=target_site,
        )

        if trunk.id in self.request.glare_trunks:
            self._event(
                "trunk",
                "glare",
                f"Glare on {trunk.label}",
                "Both ends seized the same channel at once; there is no directional "
                "assignment to break the tie.",
                trunk.id,
            )
            if trunk.signaling == "em_immediate_start":
                site = self.group.site(target_site)
                self._note(
                    "Glare on an immediate-start tie trunk destroys the outpulsed digits. "
                    "The call lands on the far-end attendant instead of the dialed station."
                )
                self._event(
                    "trunk",
                    "digits_lost",
                    "Address digits lost",
                    "The far end never received the extension digits and defaults the call "
                    "to the attendant position.",
                    trunk.id,
                )
                if site is not None:
                    self.current_site = site.id
                    return self.resolve(f"attendant:{site.id}")
            else:
                self._advance(2)
                self._note(
                    "Glare on a wink-start trunk is recoverable: the near end backs off and "
                    "retries on the next channel."
                )
                self._event(
                    "trunk",
                    "retry",
                    "Back off and retry",
                    "Wink start detects the collision before digits are sent, so the call "
                    "retries on the next free channel.",
                    trunk.id,
                )
        return None

    def _do_external(self, number: str) -> CallResult:
        if self.station_context is not None:
            restriction = self.group.cor(self.station_context.cor)
            if restriction is not None and (
                not restriction.allow_external_transfer
                or (self.carrier_channels_used > 0 and not restriction.allow_trunk_to_trunk)
            ):
                self._event(
                    "pbx",
                    "reject",
                    "External routing restricted",
                    f"{restriction.label} blocks this station's external forwarding path.",
                    f"external:{number}",
                    "rejected",
                )
                return self._finish("rejected")
        outbound = next(
            (
                trunk
                for trunk in self.group.outbound_trunks(self.current_site or "")
                if self._channel_free(trunk)
            ),
            None,
        )
        if outbound is None:
            self._event(
                "carrier",
                "congestion",
                "No outbound channel available",
                "The switch cannot originate the external leg.",
                f"external:{number}",
                "congestion",
            )
            return self._finish("congestion")
        self._hold_channel(outbound)
        self._advance(RING_CYCLE_SECONDS * 2)
        if number == self.group.answering_service:
            self._answer_at = self._answer_at or self.clock
            self._event(
                "external",
                "answer",
                "Answering service answers",
                f"The switch forwarded the call off-net to {number}; a third-party operator "
                "takes a message. The dealership sees no CDR for what was said.",
                f"external:{number}",
                "answering_service",
            )
            self._note(
                "Off-net forward to the answering service occupies two trunk channels for "
                "the whole call and leaves no in-house recording."
            )
            return self._finish("answering_service", number, "Answering service")

        self._answer_at = self._answer_at or self.clock
        self._event(
            "external",
            "answer",
            f"External number {number} answers",
            "The switch bridges an outbound leg; the inbound trunk stays up.",
            f"external:{number}",
            "answered_human",
        )
        return self._finish("answered_human", number, "External destination")

    def _do_agent(self, agent_id: str) -> CallResult:
        agent = self.group.agent(agent_id)
        if agent is None:
            return self.resolve("hangup")

        site = self.group.site(agent.site)
        if site is not None and agent.ingress in {"pstn_owned_did", "forwarded_with_diversion"}:
            # The agent is an outside number, so the switch cannot simply hand the
            # call over. It re-originates a second call across its own carrier
            # trunk group and conferences the two legs together for the duration.
            outbound = next(
                (
                    trunk
                    for trunk in self.group.outbound_trunks(site.id)
                    if self._channel_free(trunk)
                ),
                None,
            )
            if outbound is None:
                self._event(
                    "carrier",
                    "congestion",
                    "No channel left to reach the agent",
                    "The inbound leg is up but the switch has no free channel to "
                    "re-originate towards the agent's number, so the forward fails.",
                    f"agent:{agent.id}",
                    "congestion",
                )
                self._note(
                    "Forwarding to an outside number needs a second channel on the same "
                    "trunk group. On a line-constrained site this is where it breaks."
                )
                return self._finish("congestion")
            self._hold_channel(outbound)
            self._advance(2)
            self._event(
                "pbx",
                "forward",
                "Switch re-originates towards the agent",
                f"{outbound.label} carries a second leg to {agent.address}. Both legs stay "
                "up for the whole call, so this call now occupies two carrier channels.",
                outbound.id,
                carrier_channels_used=self.carrier_channels_used,
            )

        self._advance(2)
        self._answer_at = self._answer_at or self.clock
        diversion = (
            f"Diversion header carries the original DID {self.request.to_number}."
            if agent.receives_diversion
            else "No Diversion header; the agent must infer the rooftop from the trunk."
        )
        self._event(
            "agent",
            "answer",
            f"Voice agent answers ({agent.label})",
            f"Ingress mode {agent.ingress}. {diversion}",
            f"agent:{agent.id}",
            "answered_agent",
            ingress=agent.ingress,
            address=agent.address,
        )
        return self._finish("answered_agent", agent.id, agent.label)

    def _do_announce(self, announcement_id: str) -> CallResult:
        self._advance(10)
        self._answer_at = self._answer_at or self.clock
        self._event(
            "pbx",
            "announce",
            f"Announcement {announcement_id}",
            "A recorded holiday announcement plays and the switch clears the call.",
            f"announce:{announcement_id}",
            "no_answer",
        )
        self._note("Holiday announcement with no message-taking path; the caller is dropped.")
        return self._finish("no_answer")


def route(group: DealerGroup, request: CallRequest) -> CallResult:
    """Route one call through ``group``."""
    return CallRouter(group, request).run()


def route_internal(
    group: DealerGroup,
    site_id: str,
    ref: str,
    request: CallRequest,
) -> CallResult:
    """Route a leg that starts inside the switch, skipping carrier trunk seizure.

    Used for the second leg of a transfer, where the party placing the call is
    already on the PBX rather than arriving from the carrier.
    """
    router = CallRouter(group, request)
    if group.site(site_id) is None:
        raise ValueError(f"Unknown site '{site_id}'")
    router.current_site = site_id
    kind, _, value = ref.partition(":")
    target = {"ext": group.extension, "hunt": group.hunt_group, "queue": group.queue}.get(kind)
    destination = target(value) if target else None
    if destination is not None and destination.site != site_id:
        ref = f"tie:{destination.site}/{value}"
    return router.resolve(ref)
