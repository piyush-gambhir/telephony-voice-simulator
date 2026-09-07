"""Shared validation for authored AMD scenarios, before audio or calls are created.

Keep the vocabulary here so the catalog, CLI, room runner, and PSTN compiler
reject the same mistakes. IVR/PBX models have their own domain schemas.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

STEP_KEYS = {"wait", "hold", "play", "tone", "repeat_from", "hangup", "dial"}
MACHINE_OPTIONS = {"on_dtmf", "max_duration"}
EXPECT_KEYS = {
    "callee_class", "webhook_received", "ended_reason", "ended_reason_any_of",
    "ended_reason_not", "detection_layer_prefix_any_of", "detection_layer_absent",
    "dtmf_received", "dtmf_press_count_max", "agent_spoke", "first_agent_speech_after",
    "agent_speech_after", "message_start_after", "max_overlap_with_playback",
    "max_overlap_between_marks", "message_content",
}
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_DIGIT = re.compile(r"[0-9A-Da-d*#]\Z")


def _number(value: Any, location: str, *, minimum: float = 0, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location}: expected a number")
    if not math.isfinite(value) or value < minimum or (positive and value <= 0):
        raise ValueError(f"{location}: expected a finite {'positive' if positive else 'nonnegative'} number")


def _text(value: Any, location: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}: expected nonempty text")


def _identifier(value: Any, location: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{location}: use letters, digits, underscores, or hyphens")


def asset_path(assets_dir: Path, name: str) -> Path:
    """Resolve an asset within its corpus, including containment of symlinks."""
    _text(name, "play")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError(f"play: asset must be a relative corpus path: {name!r}")
    path = (assets_dir / relative).resolve()
    if not path.is_relative_to(assets_dir.resolve()):
        raise ValueError(f"play: asset escapes the corpus: {name!r}")
    return path


def validate_machine(machine: Any, *, allow_dial: bool = True) -> None:
    if not isinstance(machine, dict) or not isinstance(machine.get("main"), list):
        raise ValueError("machine.main: expected a sequence of steps")
    if "max_duration" in machine:
        _number(machine["max_duration"], "machine.max_duration", positive=True)
    sequences = set(machine) - MACHINE_OPTIONS
    for name in sequences:
        _identifier(name, "machine sequence name")
        steps = machine[name]
        if not isinstance(steps, list):
            raise ValueError(f"machine.{name}: expected a sequence of steps")
        for index, step in enumerate(steps):
            location = f"machine.{name}[{index}]"
            if not isinstance(step, dict) or len(step) != 1 or not set(step) <= STEP_KEYS:
                raise ValueError(f"{location}: expected exactly one known step ({', '.join(sorted(STEP_KEYS))})")
            kind, value = next(iter(step.items()))
            if kind in {"wait", "hold"}:
                _number(value, f"{location}.{kind}")
            elif kind == "tone":
                if value is None:
                    value = {}
                if not isinstance(value, dict) or set(value) - {"freq", "duration", "amplitude"}:
                    raise ValueError(f"{location}.tone: expected freq, duration, and amplitude options")
                for key, default in (("freq", 1000), ("duration", 0.5), ("amplitude", 12000)):
                    _number(value.get(key, default), f"{location}.tone.{key}", positive=key != "amplitude")
                if value.get("freq", 1000) >= 24000 or value.get("amplitude", 12000) > 32767:
                    raise ValueError(f"{location}.tone: frequency must be below 24000 Hz and amplitude at most 32767")
            elif kind == "play":
                _text(value, f"{location}.play")
                if Path(value).is_absolute() or ".." in Path(value).parts or "\\" in value:
                    raise ValueError(f"{location}.play: asset must be a relative corpus path")
            elif kind == "repeat_from":
                if type(value) is not int or not 0 <= value < index:
                    raise ValueError(f"{location}.repeat_from: expected an earlier step index")
            elif kind == "hangup" and value is not True:
                raise ValueError(f"{location}.hangup: expected true")
            elif kind == "dial":
                if not allow_dial:
                    raise ValueError(f"{location}.dial: bridging requires the PSTN transport")
                _text(value, f"{location}.dial")
    dtmf = machine.get("on_dtmf")
    if dtmf is None or dtmf == "ignore":
        return
    if not isinstance(dtmf, dict) or set(dtmf) - {"digits", "switch"}:
        raise ValueError("machine.on_dtmf: expected 'ignore' or digits/switch options")
    if not isinstance(dtmf.get("switch"), str) or dtmf["switch"] not in sequences:
        raise ValueError("machine.on_dtmf.switch: target sequence does not exist")
    if "digits" in dtmf:
        digits = dtmf["digits"]
        if not isinstance(digits, list) or not digits or any(
            isinstance(digit, bool) or not _DIGIT.fullmatch(str(digit)) for digit in digits
        ):
            raise ValueError("machine.on_dtmf.digits: expected a nonempty list of DTMF keys")


def validate_expectations(expect: Any) -> None:
    if not isinstance(expect, dict):
        raise ValueError("expect: expected an object")
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise ValueError(f"expect: unknown checks: {', '.join(sorted(map(str, unknown)))}")
    for key, value in expect.items():
        location = f"expect.{key}"
        if key in {"webhook_received", "detection_layer_absent", "agent_spoke"}:
            if not isinstance(value, bool):
                raise ValueError(f"{location}: expected true or false")
        elif key == "callee_class":
            if value not in ("machine", "human", "screener"):
                raise ValueError(f"{location}: expected machine, human, or screener")
        elif key == "ended_reason":
            _text(value, location)
        elif key in {"ended_reason_any_of", "ended_reason_not", "detection_layer_prefix_any_of"}:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{location}: expected a nonempty list")
            for item in value:
                _text(item, location)
        elif key == "dtmf_press_count_max":
            if type(value) is not int or value < 0:
                raise ValueError(f"{location}: expected a nonnegative integer")
        else:
            if not isinstance(value, dict):
                raise ValueError(f"{location}: expected options")
            if key == "dtmf_received":
                if set(value) - {"digit"} or ("digit" in value and not _DIGIT.fullmatch(str(value["digit"]))):
                    raise ValueError(f"{location}: expected a single DTMF digit or empty options")
            elif key.endswith("speech_after") or key == "message_start_after":
                if set(value) - {"mark", "min", "max"}:
                    raise ValueError(f"{location}: expected mark, min, and max options")
                _text(value.get("mark", "tone_end"), f"{location}.mark")
                for bound in ("min", "max"):
                    _number(value.get(bound, 0 if bound == "min" else 5), f"{location}.{bound}")
                if value.get("min", 0) > value.get("max", 5):
                    raise ValueError(f"{location}: min must not exceed max")
            elif key.startswith("max_overlap_"):
                fields = {"asset", "max_s"} if key.endswith("playback") else {"start", "end", "max_s"}
                if set(value) - fields:
                    raise ValueError(f"{location}: unknown overlap options")
                if key.endswith("playback"):
                    _text(value.get("asset"), f"{location}.asset")
                for mark in ("start", "end"):
                    if mark in value:
                        _text(value[mark], f"{location}.{mark}")
                _number(value.get("max_s", 0.5), f"{location}.max_s")
            elif key == "message_content":
                if set(value) - {"expected", "min_recall", "head_words", "max_preamble_words", "allowed_identity_name"}:
                    raise ValueError(f"{location}: unknown message content options")
                _text(value.get("expected"), f"{location}.expected")
                _number(value.get("min_recall", 0.75), f"{location}.min_recall")
                if value.get("min_recall", 0.75) > 1:
                    raise ValueError(f"{location}.min_recall: must not exceed 1")
                for key, minimum in (("head_words", 1), ("max_preamble_words", 0)):
                    if key in value and (type(value[key]) is not int or value[key] < minimum):
                        raise ValueError(f"{location}.{key}: expected an integer >= {minimum}")


def validate_scenario(scenario: Any, *, source: str = "scenario") -> dict[str, Any]:
    """Validate and return an unchanged scenario; errors include their source."""
    try:
        if not isinstance(scenario, dict):
            raise ValueError("expected a scenario object")
        _identifier(scenario.get("name"), "name")
        validate_machine(scenario.get("machine"))
        validate_expectations(scenario.get("expect"))
        for key in ("answer_delay", "call_timeout", "webhook_timeout"):
            if key in scenario:
                _number(scenario[key], key, positive=key != "answer_delay")
        if "pstn_only" in scenario and not isinstance(scenario["pstn_only"], bool):
            raise ValueError("pstn_only: expected true or false")
        if "config_overrides" in scenario and not isinstance(scenario["config_overrides"], dict):
            raise ValueError("config_overrides: expected an object")
    except ValueError as error:
        raise ValueError(f"{source}: {error}") from error
    return scenario
