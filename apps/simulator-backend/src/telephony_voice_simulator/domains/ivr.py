"""Provider-neutral model of the inbound extension IVR."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from .base import ScenarioDefinition, TimelineStep

Disposition = Literal["answer", "busy", "no_answer", "failed"]
FreeFormDisposition = Literal["connected", "busy", "no_answer", "failed", "abandoned"]


@dataclass(frozen=True)
class Destination:
    label: str
    number: str
    disposition: Disposition = "answer"


@dataclass(frozen=True)
class IvrScenario:
    """One complete caller journey through the extension menu.

    ``name`` keeps the IDs exposed by the original telephony simulator.  The
    newer unified-repository IDs remain accepted through ``aliases``.
    """

    name: str
    title: str
    caller_number: str
    public_number: str
    inputs: tuple[str | None, ...]
    destinations: dict[str, Destination]
    expected_outcome: str
    aliases: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        """Compatibility with the original simulator's ``Scenario.id``."""
        return self.name


DEFAULT_DESTINATIONS = {
    "1501": Destination("Extension 1501 cell", "+15550101501"),
    "1502": Destination("Extension 1502 cell", "+15550101502"),
}


def _destinations_with(ext: str, disposition: Disposition) -> dict[str, Destination]:
    destinations = dict(DEFAULT_DESTINATIONS)
    current = destinations[ext]
    destinations[ext] = Destination(current.label, current.number, disposition)
    return destinations


SCENARIOS = (
    IvrScenario(
        "connect-1501",
        "Caller reaches extension 1501",
        "+14085550131",
        "+19085550168",
        ("1501",),
        DEFAULT_DESTINATIONS,
        "bridged",
        ("ivr_connect_extension", "ivr_connect_extension_1501"),
    ),
    IvrScenario(
        "retry-after-wrong-extension",
        "Wrong extension, then a valid one",
        "+16505550168",
        "+19085550168",
        ("9999", "1501"),
        DEFAULT_DESTINATIONS,
        "bridged",
        ("ivr_retry_wrong_extension",),
    ),
    IvrScenario(
        "retry-after-silence",
        "Silent caller retries the menu",
        "+15105550199",
        "+19085550168",
        (None, "1502"),
        DEFAULT_DESTINATIONS,
        "bridged",
        ("ivr_retry_after_silence",),
    ),
    IvrScenario(
        "destination-busy",
        "Extension destination is busy",
        "+19255550144",
        "+19085550168",
        ("1501",),
        _destinations_with("1501", "busy"),
        "dial_busy",
        ("ivr_destination_busy",),
    ),
    IvrScenario(
        "destination-no-answer",
        "Extension destination does not answer",
        "+12065550117",
        "+19085550168",
        ("1502",),
        _destinations_with("1502", "no_answer"),
        "dial_no_answer",
        ("ivr_destination_no_answer",),
    ),
    IvrScenario(
        "destination-failed",
        "Carrier cannot complete extension dial",
        "+12125550126",
        "+19085550168",
        ("1501",),
        _destinations_with("1501", "failed"),
        "dial_failed",
        ("ivr_destination_failed",),
    ),
    IvrScenario(
        "caller-abandons",
        "Caller abandons after menu errors",
        "+13125550175",
        "+19085550168",
        ("1234", None, "0000"),
        DEFAULT_DESTINATIONS,
        "abandoned",
        ("ivr_caller_abandons",),
    ),
)


def get_scenario(identifier: str) -> IvrScenario | None:
    return next(
        (
            scenario
            for scenario in SCENARIOS
            if identifier == scenario.name or identifier in scenario.aliases
        ),
        None,
    )


def simulate(scenario: IvrScenario) -> tuple[tuple[TimelineStep, ...], str]:
    steps = [
        TimelineStep(
            "00:00",
            "caller",
            "call",
            "Caller dials the public line",
            (
                f"{scenario.caller_number} calls {scenario.public_number}; "
                "the provider sends the inbound call to the IVR."
            ),
            metadata={
                "leg": "PSTN inbound",
                "destination": scenario.public_number,
                "caller_number": scenario.caller_number,
            },
        )
    ]
    second = 1

    def stamp() -> str:
        nonlocal second
        value = f"{second // 60:02d}:{second % 60:02d}"
        second += 1
        return value

    for attempt, entered in enumerate(scenario.inputs, start=1):
        steps.append(
            TimelineStep(
                stamp(),
                "ivr",
                "gather",
                "Play menu and gather digits",
                "The IVR plays its welcome prompt and gathers up to four DTMF digits.",
                metadata={"attempt": attempt, "leg": "IVR", "destination": "extension directory"},
            )
        )
        if entered is None:
            steps.extend(
                (
                    TimelineStep(
                        stamp(),
                        "caller",
                        "silence",
                        "Caller stays silent",
                        "No digits arrive before the gather timeout.",
                        metadata={"attempt": attempt, "leg": "IVR", "destination": "menu retry"},
                    ),
                    TimelineStep(
                        stamp(),
                        "ivr",
                        "retry",
                        "Play no-input prompt",
                        "The IVR explains the timeout and returns to the menu.",
                        metadata={"attempt": attempt, "leg": "IVR", "destination": "main menu"},
                    ),
                )
            )
            continue
        if re.fullmatch(r"\d{4}", entered) is None:
            raise ValueError("IVR extensions must contain exactly 4 digits")

        steps.append(
            TimelineStep(
                stamp(),
                "caller",
                "dtmf",
                f"Caller enters {entered}",
                "The gathered digits are submitted to the extension directory.",
                metadata={
                    "digits": entered,
                    "attempt": attempt,
                    "leg": "IVR",
                    "destination": "extension directory",
                },
            )
        )
        destination = scenario.destinations.get(entered)
        if destination is None:
            steps.append(
                TimelineStep(
                    stamp(),
                    "ivr",
                    "retry",
                    "Extension not found",
                    "The IVR plays the not-found prompt and returns to the menu.",
                    metadata={"attempt": attempt, "leg": "IVR", "destination": "main menu"},
                )
            )
            continue

        steps.extend(
            (
                TimelineStep(
                    stamp(),
                    "ivr",
                    "play",
                    "Play connecting prompt",
                    f"The IVR prepares to connect {destination.label}.",
                    metadata={"leg": "IVR", "destination": destination.label},
                ),
                TimelineStep(
                    stamp(),
                    "provider",
                    "dial",
                    f"Dial {destination.label}",
                    f"The provider starts an outbound leg to {destination.number}.",
                    metadata={
                        "leg": "PSTN outbound",
                        "destination": destination.number,
                        "caller_id": scenario.public_number,
                    },
                ),
            )
        )
        if destination.disposition == "answer":
            steps.append(
                TimelineStep(
                    stamp(),
                    "callee",
                    "bridge",
                    "Destination answers",
                    "The inbound and destination media legs are bridged.",
                    "bridged",
                    {
                        "leg": "Bridged media",
                        "destination": destination.label,
                        "connected_to": destination.label,
                    },
                )
            )
            return tuple(steps), "bridged"

        outcome = f"dial_{destination.disposition}"
        steps.append(
            TimelineStep(
                stamp(),
                "callee",
                "hangup",
                f"Destination {destination.disposition.replace('_', ' ')}",
                "The destination leg does not bridge and the call ends.",
                outcome,
                {"leg": "PSTN outbound", "destination": destination.number},
            )
        )
        return tuple(steps), outcome

    steps.append(
        TimelineStep(
            stamp(),
            "caller",
            "hangup",
            "Caller abandons",
            "The caller exhausts the available menu attempts.",
            "abandoned",
            {"leg": "PSTN inbound", "destination": scenario.public_number},
        )
    )
    return tuple(steps), "abandoned"


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
                kind="ivr",
                description="Exercises inbound menu input, retry, dialing, and bridge behavior.",
                steps=steps,
                expected_outcome=outcome,
                has_dtmf=True,
                aliases=scenario.aliases,
                metadata={
                    "caller_number": scenario.caller_number,
                    "public_number": scenario.public_number,
                    "attempts": len(scenario.inputs),
                },
            )
        )
    return tuple(result)


def simulate_directory_call(
    *,
    caller_number: str,
    public_number: str,
    extension: str,
    destination: Destination | None,
    ring_timeout: int = 25,
    forced_disposition: FreeFormDisposition = "connected",
) -> tuple[tuple[TimelineStep, ...], str]:
    """Run a free-form IVR directory call on the shared timeline model."""
    if re.fullmatch(r"\d{4}", extension) is None:
        raise ValueError("IVR extensions must contain exactly 4 digits")
    steps = [
        TimelineStep(
            "00:00",
            "caller",
            "call",
            "Caller dials the public line",
            f"{caller_number} starts an inbound call to {public_number}.",
            metadata={
                "leg": "PSTN inbound",
                "destination": public_number,
                "caller_number": caller_number,
            },
        ),
        TimelineStep(
            "00:01",
            "provider",
            "normalize",
            "Normalize inbound event",
            "The provider payload becomes a provider-neutral inbound.call event.",
            metadata={"leg": "Provider webhook", "destination": "call engine"},
        ),
        TimelineStep(
            "00:02",
            "ivr",
            "gather",
            "Play menu and gather digits",
            "The IVR requests an extension and gathers DTMF input.",
            metadata={"leg": "IVR", "destination": "extension directory"},
        ),
        TimelineStep(
            "00:06",
            "caller",
            "dtmf",
            f"Caller enters {extension}",
            (
                f"The directory resolves {destination.label}."
                if destination
                else "No active directory entry matches the digits."
            ),
            metadata={
                "digits": extension,
                "leg": "IVR",
                "destination": destination.label if destination else "unmatched extension",
            },
        ),
    ]
    if destination is None:
        steps.append(
            TimelineStep(
                "00:07",
                "ivr",
                "hangup",
                "Extension not found",
                "The route cannot be completed.",
                "failed",
                {"leg": "IVR", "destination": "hangup"},
            )
        )
        return tuple(steps), "failed"

    steps.append(
        TimelineStep(
            "00:07",
            "provider",
            "dial",
            f"Dial {destination.label}",
            (
                f"The provider starts an outbound leg to {destination.number} "
                f"with a {ring_timeout}s timeout."
            ),
            metadata={"leg": "PSTN outbound", "destination": destination.number},
        )
    )
    if forced_disposition == "connected":
        steps.append(
            TimelineStep(
                "00:12",
                "callee",
                "bridge",
                "Destination answers",
                "The inbound and destination media legs are bridged.",
                "bridged",
                {"leg": "Bridged media", "destination": destination.number},
            )
        )
        return tuple(steps), "bridged"

    if forced_disposition == "abandoned":
        steps.append(
            TimelineStep(
                "00:12",
                "caller",
                "hangup",
                "Caller abandons",
                "The caller hangs up before the destination answers.",
                "abandoned",
                {"leg": "PSTN inbound", "destination": public_number},
            )
        )
        return tuple(steps), "abandoned"

    outcome = f"dial_{forced_disposition}"
    steps.append(
        TimelineStep(
            "00:12",
            "callee",
            "hangup",
            f"Destination {forced_disposition.replace('_', ' ')}",
            "The destination leg does not bridge and the call ends.",
            outcome,
            {"leg": "PSTN outbound", "destination": destination.number},
        )
    )
    return tuple(steps), outcome


# Lower-case fixture name retained for scripts importing the old package.
scenarios = SCENARIOS
