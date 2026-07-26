"""PBX routing scenarios on the shared telephony timeline model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .base import ScenarioDefinition, TimelineStep

ExtensionState = Literal["available", "busy", "offline", "after_hours"]
OfficeState = Literal["open", "after_hours", "holiday"]


@dataclass(frozen=True)
class Department:
    id: str
    label: str
    ivr_digit: str
    queue_name: str
    ring_strategy: str
    overflow_target: str
    sla_seconds: int

    @property
    def digit(self) -> str:
        """Compatibility with the first unified PBX model."""
        return self.ivr_digit


@dataclass(frozen=True)
class Extension:
    id: str
    name: str
    ext: str
    department: str
    role: str
    device: str
    state: ExtensionState

    @property
    def number(self) -> str:
        """Compatibility with the first unified PBX model."""
        return self.ext


@dataclass(frozen=True)
class PbxScenario:
    name: str
    title: str
    caller: str
    caller_number: str
    intent: str
    dialed_number: str
    office_state: OfficeState
    ivr_input: str
    expected_outcome: str
    expected_summary: str
    initial_extensions: dict[str, ExtensionState] = field(default_factory=dict)
    direct_extension: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return self.name

    @property
    def input(self) -> str:
        return self.ivr_input

    @property
    def states(self) -> dict[str, ExtensionState]:
        return self.initial_extensions


DEPARTMENTS = (
    Department("reception", "Reception", "0", "Front desk", "reception-first", "voicemail", 20),
    Department("sales", "Sales", "1", "Sales floor", "simultaneous", "reception", 30),
    Department(
        "service",
        "Service",
        "2",
        "Service advisors",
        "round-robin",
        "external_on_call",
        45,
    ),
    Department("parts", "Parts", "3", "Parts counter", "least-recent", "voicemail", 35),
    Department("finance", "Finance", "4", "F&I office", "round-robin", "reception", 30),
    Department("accounting", "Accounting", "5", "Back office", "least-recent", "voicemail", 25),
)

EXTENSIONS = (
    Extension(
        "frontdesk",
        "Front desk",
        "100",
        "reception",
        "Receptionist",
        "desk phone",
        "available",
    ),
    Extension(
        "sales-a",
        "Sales consultant",
        "201",
        "sales",
        "Sales consultant",
        "softphone",
        "available",
    ),
    Extension(
        "sales-b",
        "Sales manager",
        "202",
        "sales",
        "Sales manager",
        "mobile app",
        "busy",
    ),
    Extension(
        "service-a",
        "Service advisor",
        "301",
        "service",
        "Service advisor",
        "desk phone",
        "available",
    ),
    Extension(
        "service-b",
        "Warranty advisor",
        "302",
        "service",
        "Warranty advisor",
        "softphone",
        "offline",
    ),
    Extension(
        "parts-a",
        "Parts counter",
        "401",
        "parts",
        "Parts counter",
        "shared phone",
        "available",
    ),
    Extension(
        "finance-a",
        "Finance manager",
        "501",
        "finance",
        "Finance manager",
        "softphone",
        "available",
    ),
    Extension(
        "accounting-a",
        "Accounts payable",
        "601",
        "accounting",
        "Accounts payable",
        "desk phone",
        "after_hours",
    ),
)

SCENARIOS = (
    PbxScenario(
        "sales-mainline-open",
        "Main line to sales, agent answers",
        "Website lead",
        "+14085550131",
        "Wants price and availability",
        "+14155550100",
        "open",
        "1",
        "connected",
        "Sales consultant answers and CRM activity is created.",
        {"sales-a": "available", "sales-b": "busy"},
        aliases=("pbx_sales_queue_answers",),
    ),
    PbxScenario(
        "service-overflow",
        "Service queue overflow to on-call",
        "Existing customer",
        "+15105550199",
        "Needs an urgent service appointment update",
        "+14155550100",
        "open",
        "2",
        "overflow",
        "Service queue overflows to the external on-call number.",
        {"service-a": "busy", "service-b": "offline"},
        aliases=("pbx_service_queue_overflow",),
    ),
    PbxScenario(
        "direct-extension-busy",
        "Direct extension busy, fallback to receptionist",
        "Repeat caller",
        "+16505550168",
        "Calls a known sales-manager extension",
        "+14155550100",
        "open",
        "202",
        "connected",
        "PBX rings extension 202, then returns to reception.",
        {"sales-b": "busy", "frontdesk": "available"},
        "202",
        ("pbx_direct_extension_fallback",),
    ),
    PbxScenario(
        "after-hours-voicemail",
        "After-hours main line",
        "Late-night caller",
        "+19255550144",
        "Asks for business hours and a callback",
        "+14155550100",
        "after_hours",
        "1",
        "voicemail",
        "Caller leaves voicemail and a CRM callback task is queued.",
        {
            "frontdesk": "after_hours",
            "sales-a": "after_hours",
            "sales-b": "after_hours",
        },
        aliases=("pbx_after_hours_voicemail",),
    ),
)


def get_scenario(identifier: str) -> PbxScenario | None:
    return next(
        (
            scenario
            for scenario in SCENARIOS
            if identifier == scenario.name or identifier in scenario.aliases
        ),
        None,
    )


def state_for(
    extension: Extension,
    overrides: dict[str, ExtensionState] | None = None,
) -> ExtensionState:
    return (overrides or {}).get(extension.id, extension.state)


def by_department(department_id: str) -> tuple[Extension, ...]:
    return tuple(extension for extension in EXTENSIONS if extension.department == department_id)


def _step(
    at: str,
    actor: str,
    kind: str,
    label: str,
    detail: str,
    leg: str,
    destination: str,
    outcome: str = "in_progress",
    **metadata: Any,
) -> TimelineStep:
    return TimelineStep(
        at,
        actor,
        kind,
        label,
        detail,
        outcome,
        {"leg": leg, "destination": destination, **metadata},
    )


def _cdr_step(at: str, outcome: str, destination: str) -> TimelineStep:
    return _step(
        at,
        "pbx",
        "cdr",
        "CDR finalized",
        "The PBX writes answer, routing, duration, recording, and disposition metadata.",
        "Call detail record",
        destination,
        outcome,
    )


def build_timeline(
    scenario: PbxScenario,
    overrides: dict[str, ExtensionState] | None = None,
) -> tuple[TimelineStep, ...]:
    """Build a PBX timeline, applying per-run extension-state overrides."""
    states = {**scenario.initial_extensions, **(overrides or {})}
    steps = [
        _step(
            "00:00",
            "caller",
            "call",
            "Caller dials public main line",
            f"{scenario.caller_number} calls {scenario.dialed_number}: {scenario.intent}.",
            "PSTN inbound",
            scenario.dialed_number,
            caller=scenario.caller,
            caller_number=scenario.caller_number,
            intent=scenario.intent,
        ),
        _step(
            "00:01",
            "carrier",
            "route",
            "Carrier resolves DID",
            "The provider maps the DID to the business SIP trunk and sends caller ID.",
            "PSTN -> SIP",
            "primary SIP trunk",
        ),
        _step(
            "00:02",
            "sip",
            "invite",
            "SIP INVITE reaches PBX",
            "The PBX creates a call session, applies the inbound route, and starts CDR logging.",
            "SIP trunk",
            "PBX inbound route",
            cdr_status="started",
        ),
    ]

    if scenario.office_state != "open":
        steps.extend(
            (
                _step(
                    "00:03",
                    "pbx",
                    "schedule",
                    "After-hours schedule matched",
                    "Live ring groups are skipped; the caller hears the closed greeting.",
                    "PBX schedule",
                    "after-hours IVR",
                ),
                _step(
                    "00:09",
                    "ivr",
                    "record",
                    "Voicemail captures message",
                    "The PBX records voicemail and tags it with main-line caller ID.",
                    "Voicemail",
                    "department mailbox",
                    "voicemail",
                ),
                _step(
                    "00:13",
                    "crm",
                    "callback",
                    "Callback task created",
                    "CRM receives caller ID, recording URL, transcript status, and owner.",
                    "Webhook",
                    "CRM callback queue",
                    "callback_queued",
                ),
                _cdr_step("00:14", "voicemail", "department mailbox"),
            )
        )
        return tuple(steps)

    steps.append(
        _step(
            "00:03",
            "ivr",
            "answer",
            "Main greeting answers",
            "The IVR offers departments and accepts direct extension entry.",
            "PBX media",
            "main IVR",
        )
    )

    direct_target = next(
        (extension for extension in EXTENSIONS if extension.ext == scenario.direct_extension),
        None,
    )
    if direct_target:
        direct_state = state_for(direct_target, states)
        steps.append(
            _step(
                "00:07",
                "ivr",
                "dtmf",
                f"Caller enters extension {direct_target.ext}",
                f"The PBX attempts {direct_target.name} on {direct_target.device}.",
                "Direct extension",
                f"{direct_target.name} x{direct_target.ext}",
                digits=direct_target.ext,
                role=direct_target.role,
                device=direct_target.device,
                extension_state=direct_state,
            )
        )
        if direct_state == "available":
            steps.extend(
                (
                    _step(
                        "00:12",
                        "agent",
                        "bridge",
                        "Extension answers",
                        "The PBX bridges caller media and starts call recording.",
                        "Extension bridge",
                        direct_target.name,
                        "connected",
                        role=direct_target.role,
                        device=direct_target.device,
                    ),
                    _step(
                        "00:15",
                        "crm",
                        "activity",
                        "CRM activity emitted",
                        "The webhook records caller, extension, answer latency, and recording state.",
                        "Webhook",
                        "CRM activity",
                        "activity_created",
                    ),
                    _cdr_step("00:16", "connected", direct_target.name),
                )
            )
            return tuple(steps)
        steps.extend(
            (
                _step(
                    "00:12",
                    "extension",
                    "fallback",
                    f"Extension {direct_state.replace('_', ' ')}",
                    "No-answer policy sends the caller to reception fallback.",
                    "Extension ring",
                    direct_target.name,
                    "fallback",
                    role=direct_target.role,
                    device=direct_target.device,
                    extension_state=direct_state,
                ),
                _step(
                    "00:18",
                    "agent",
                    "bridge",
                    "Receptionist answers",
                    "The operator sees the original extension and caller ID before taking the call.",
                    "Operator bridge",
                    "Reception",
                    "connected",
                    original_extension=direct_target.ext,
                ),
                _step(
                    "00:21",
                    "crm",
                    "activity",
                    "CRM fallback activity emitted",
                    "The activity preserves the requested extension and receptionist fallback.",
                    "Webhook",
                    "CRM activity",
                    "activity_created",
                ),
                _cdr_step("00:22", "connected", "Reception"),
            )
        )
        return tuple(steps)

    department = next(
        (item for item in DEPARTMENTS if item.ivr_digit == scenario.ivr_input),
        DEPARTMENTS[0],
    )
    department_extensions = by_department(department.id)
    available = next(
        (extension for extension in department_extensions if state_for(extension, states) == "available"),
        None,
    )
    steps.extend(
        (
            _step(
                "00:07",
                "caller",
                "dtmf",
                f"Caller presses {department.ivr_digit}",
                f"The PBX routes to {department.label} using {department.ring_strategy}.",
                "IVR selection",
                department.queue_name,
                digits=department.ivr_digit,
                department=department.id,
                ring_strategy=department.ring_strategy,
            ),
            _step(
                "00:09",
                "queue",
                "queue",
                f"{department.queue_name} queue starts",
                f"Eligible extensions ring while the PBX measures a {department.sla_seconds}s SLA.",
                "Queue",
                department.queue_name,
                department=department.id,
                sla_seconds=department.sla_seconds,
            ),
        )
    )

    if available:
        steps.extend(
            (
                _step(
                    "00:15",
                    "extension",
                    "ring",
                    f"{available.name} rings",
                    "The agent device receives caller ID, queue name, and screen-pop context.",
                    "Ring group",
                    f"{available.name} x{available.ext}",
                    role=available.role,
                    device=available.device,
                    extension=available.ext,
                    extension_state=state_for(available, states),
                ),
                _step(
                    "00:18",
                    "agent",
                    "bridge",
                    "Agent answers",
                    "The PBX bridges RTP audio and starts recording after answer.",
                    "Bridged media",
                    available.name,
                    "connected",
                    role=available.role,
                    device=available.device,
                    extension=available.ext,
                ),
                _step(
                    "00:21",
                    "crm",
                    "activity",
                    "CRM activity emitted",
                    "The webhook posts call, department, agent, caller, and answer-latency metadata.",
                    "Webhook",
                    "CRM activity",
                    "activity_created",
                ),
                _cdr_step("00:22", "connected", available.name),
            )
        )
        return tuple(steps)

    overflow = department.overflow_target.replace("_", " ")
    steps.extend(
        (
            _step(
                "00:54",
                "queue",
                "overflow",
                "Queue SLA exceeded",
                "No eligible extension answered; the PBX applies the overflow policy.",
                "Queue timeout",
                overflow,
                "overflow",
                department=department.id,
                sla_seconds=department.sla_seconds,
            ),
            _step(
                "00:57",
                "sip" if department.overflow_target == "external_on_call" else "ivr",
                "route",
                "Overflow target dialed",
                "The PBX keeps the inbound leg alive and moves the caller to the fallback.",
                "Overflow route",
                overflow,
                "overflow",
            ),
            _step(
                "01:04",
                "crm",
                "callback",
                "Missed-call workflow",
                "CRM receives missed department, timeout reason, queue wait, and callback owner.",
                "Webhook",
                "CRM callback workflow",
                "follow_up_queued",
            ),
            _cdr_step("01:05", "overflow", overflow),
        )
    )
    return tuple(steps)


def simulate(
    scenario: PbxScenario,
    overrides: dict[str, ExtensionState] | None = None,
) -> tuple[tuple[TimelineStep, ...], str]:
    steps = build_timeline(scenario, overrides)
    if scenario.office_state != "open":
        return steps, "voicemail"
    if scenario.direct_extension:
        return steps, "connected"
    department = next(
        (item for item in DEPARTMENTS if item.ivr_digit == scenario.ivr_input),
        DEPARTMENTS[0],
    )
    states = {**scenario.initial_extensions, **(overrides or {})}
    has_available = any(
        state_for(extension, states) == "available" for extension in by_department(department.id)
    )
    return steps, "connected" if has_available else "overflow"


def definitions() -> tuple[ScenarioDefinition, ...]:
    result = []
    for scenario in SCENARIOS:
        steps, outcome = simulate(scenario)
        if outcome != scenario.expected_outcome:
            raise ValueError(f"{scenario.name}: expected {scenario.expected_outcome}, got {outcome}")
        result.append(
            ScenarioDefinition(
                name=scenario.name,
                title=scenario.title,
                kind="pbx",
                description="Exercises schedules, SIP legs, queues, extensions, CRM, and CDRs.",
                steps=steps,
                expected_outcome=outcome,
                has_dtmf=True,
                aliases=scenario.aliases,
                metadata={
                    "caller": scenario.caller,
                    "caller_number": scenario.caller_number,
                    "intent": scenario.intent,
                    "dialed_number": scenario.dialed_number,
                    "office_state": scenario.office_state,
                    "expected_summary": scenario.expected_summary,
                    "initial_extensions": scenario.initial_extensions,
                },
            )
        )
    return tuple(result)


# Original fixture names retained for simple imports and scripts.
departments = DEPARTMENTS
extensions = EXTENSIONS
scenarios = SCENARIOS
