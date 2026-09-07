"""Per-scenario assertions over the machine timeline, the agent's recorded
audio, and the captured ``call.ended`` webhook.

Everything shares one clock: machine-timeline seconds (t=0 when the machine
script started). Track recordings carry their own ``started_at`` offset in a
sidecar ``.meta.json``, so agent speech segments are converted into the same
axis before timing checks.

Scenario ``expect`` vocabulary (all keys optional):

    ended_reason: call.ending.voicemail-left-message      # exact...
    ended_reason_any_of: [a, b]                           # ...or one of
    ended_reason_not: [call.ending.machine-ivr]           # ...or none of
    detection_layer_prefix_any_of: [livekit-amd, semantic]
    detection_layer_absent: true                          # human paths
    dtmf_received: {digit: "5"}                           # timeline check
    dtmf_press_count_max: 2
    agent_spoke: true                                     # any speech at all
    first_agent_speech_after: {mark: audio_path_ready, max: 5.0}
    agent_speech_after: {mark: play_end:human.wav, max: 4.0}
    message_start_after: {mark: tone_end, min: 0.2, max: 3.0}
    message_start_after: {mark: play_end:beep.wav, min: 0.2, max: 3.0}
    max_overlap_with_playback: {asset: greeting.wav, max_s: 0.5}
    webhook_received: true                                # call.ended landed
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .scenario_validation import validate_expectations

# Simple energy VAD over the agent's recorded track. The agent's TTS is loud
# and clean (it is the room's direct audio, not a mic), so a fixed int16 RMS
# threshold is reliable; ambience tracks stay far below it.
VAD_FRAME_S = 0.02
VAD_RMS_THRESHOLD = 700.0
VAD_MERGE_GAP_S = 0.35
VAD_MIN_SEGMENT_S = 0.25


@dataclass
class SpeechSegment:
    start: float  # machine-timeline seconds
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class CheckResult:
    name: str
    passed: bool | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


def summarize_checks(results: Iterable[bool | None]) -> bool | None:
    """A failure takes precedence; missing evidence can never produce a pass."""
    values = tuple(results)
    if any(value is False for value in values):
        return False
    return True if values and all(value is True for value in values) else None


def speech_segments_from_wav(wav_path: Path) -> list[SpeechSegment]:
    """Agent speech segments in machine-timeline seconds."""
    import soundfile as sf

    meta_path = wav_path.with_suffix(".meta.json")
    offset = 0.0
    if meta_path.exists():
        offset = float(json.loads(meta_path.read_text()).get("started_at", 0.0))
    data, rate = sf.read(wav_path, dtype="int16", always_2d=True)
    samples = data[:, 0].astype(np.float64)
    frame_n = max(1, int(VAD_FRAME_S * rate))
    n_frames = len(samples) // frame_n
    if n_frames == 0:
        return []
    rms = np.sqrt(
        np.mean(samples[: n_frames * frame_n].reshape(n_frames, frame_n) ** 2, axis=1)
    )
    voiced = rms >= VAD_RMS_THRESHOLD

    segments: list[SpeechSegment] = []
    start: int | None = None
    last_voiced: int | None = None
    for i, v in enumerate(voiced):
        if v:
            if start is None:
                start = i
            last_voiced = i
        elif start is not None and last_voiced is not None and (i - last_voiced) * VAD_FRAME_S > VAD_MERGE_GAP_S:
            segments.append(
                SpeechSegment(offset + start * VAD_FRAME_S, offset + (last_voiced + 1) * VAD_FRAME_S)
            )
            start = None
            last_voiced = None
    if start is not None and last_voiced is not None:
        segments.append(
            SpeechSegment(offset + start * VAD_FRAME_S, offset + (last_voiced + 1) * VAD_FRAME_S)
        )
    return [s for s in segments if s.duration >= VAD_MIN_SEGMENT_S]


def merged_agent_segments(recordings: dict[str, Path]) -> list[SpeechSegment]:
    segments: list[SpeechSegment] = []
    for path in recordings.values():
        if path.exists():
            segments.extend(speech_segments_from_wav(path))
    merged: list[SpeechSegment] = []
    for segment in sorted(segments, key=lambda s: s.start):
        if merged and segment.start <= merged[-1].end:
            merged[-1].end = max(merged[-1].end, segment.end)
        else:
            merged.append(segment)
    return merged


def _timeline_marks(timeline: list[dict], event: str, **match: Any) -> list[float]:
    out = []
    if event.startswith("play_end:"):
        asset = event.removeprefix("play_end:")
        return _timeline_marks(timeline, "play_end", asset=asset)
    for entry in timeline:
        if entry.get("event") != event:
            continue
        if all(entry.get(k) == v for k, v in match.items()):
            out.append(float(entry["t"]))
    return out


def _playback_intervals(timeline: list[dict], asset: str) -> list[tuple[float, float]]:
    intervals = []
    start = None
    for entry in timeline:
        if entry.get("asset") != asset:
            continue
        if entry.get("event") == "play_start":
            start = float(entry["t"])
        elif entry.get("event") in {"play_end", "play_interrupted"} and start is not None:
            end = float(entry["t"])
            if end >= start:
                intervals.append((start, end))
            start = None
    return intervals


def run_checks(
    expect: dict[str, Any],
    *,
    timeline: list[dict],
    recordings: dict[str, Path],
    call_ended: dict[str, Any] | None,
) -> list[CheckResult]:
    validate_expectations(expect)
    results: list[CheckResult] = []
    audio_checks = set(expect) & {
        "agent_spoke", "first_agent_speech_after", "agent_speech_after", "message_start_after",
        "max_overlap_with_playback", "max_overlap_between_marks",
    }
    segments: list[SpeechSegment] = []
    audio_error = None
    if audio_checks:
        if not recordings or any(not path.is_file() for path in recordings.values()):
            audio_error = "Agent recording unavailable; silence and timing are unverified"
        elif any(entry.get("event") == "agent_track_recording_error" for entry in timeline):
            audio_error = "Agent recording failed; silence and timing are unverified"
        else:
            try:
                segments = merged_agent_segments(recordings)
            except (OSError, RuntimeError, ValueError) as error:
                audio_error = f"Agent recording unreadable: {error}"
    if audio_error:
        for key in sorted(audio_checks):
            results.append(CheckResult(key, None, audio_error))
        expect = {key: value for key, value in expect.items() if key not in audio_checks}
    ended_reason = ""
    layer = ""
    if call_ended:
        data = call_ended.get("data") or {}
        ended_reason = str(data.get("ended_reason") or call_ended.get("ended_reason") or "")
        meta = data.get("provider_metadata") or data.get("metadata") or {}
        layer = str(
            data.get("voicemail_detection_layer")
            or meta.get("voicemail_detection_layer")
            or ""
        )

    def add(name: str, passed: bool | None, detail: str) -> None:
        results.append(CheckResult(name, passed, detail))

    if "webhook_received" in expect:
        received = call_ended is not None
        add("webhook_received", received == expect["webhook_received"], f"call.ended={'yes' if received else 'MISSING'}")

    if "ended_reason" in expect:
        want = str(expect["ended_reason"])
        add("ended_reason", ended_reason == want, f"want={want} got={ended_reason or '(none)'}")
    if "ended_reason_any_of" in expect:
        want = [str(w) for w in expect["ended_reason_any_of"]]
        add("ended_reason_any_of", ended_reason in want, f"want∈{want} got={ended_reason or '(none)'}")
    if "ended_reason_not" in expect:
        banned = [str(w) for w in expect["ended_reason_not"]]
        add("ended_reason_not", bool(ended_reason) and ended_reason not in banned, f"banned={banned} got={ended_reason or '(none)'}")

    if "detection_layer_prefix_any_of" in expect:
        prefixes = [str(p) for p in expect["detection_layer_prefix_any_of"]]
        ok = any(layer.startswith(p) for p in prefixes)
        add("detection_layer", ok, f"want prefix∈{prefixes} got={layer or '(none)'}")
    if "detection_layer_absent" in expect:
        absent = not layer
        add("detection_layer_absent", call_ended is not None and absent == expect["detection_layer_absent"], f"got={layer or '(none)'} webhook={'yes' if call_ended is not None else 'MISSING'}")

    # callee_class labels benchmark ground truth, rather than an assertion.
    # Room mode has no isolated mailbox transcript; keep the requested
    # content check visible instead of silently treating it as successful.
    if "message_content" in expect:
        add("message_content", None, "Message content requires PSTN recording/transcription grading")

    if "dtmf_received" in expect:
        spec = expect["dtmf_received"] or {}
        digit = str(spec.get("digit", ""))
        hits = _timeline_marks(timeline, "dtmf_received", **({"digit": digit} if digit else {}))
        add("dtmf_received", bool(hits), f"digit={digit or 'any'} presses_at={hits}")
    if "dtmf_press_count_max" in expect:
        count = len(_timeline_marks(timeline, "dtmf_received"))
        limit = int(expect["dtmf_press_count_max"])
        add("dtmf_press_count", count <= limit, f"count={count} max={limit}")

    if expect.get("agent_spoke") is not None:
        want = bool(expect["agent_spoke"])
        add("agent_spoke", bool(segments) == want, f"segments={len(segments)}")

    if "first_agent_speech_after" in expect:
        spec = expect["first_agent_speech_after"] or {}
        mark_name = str(spec.get("mark", "audio_path_ready"))
        marks = _timeline_marks(timeline, mark_name)
        if not marks:
            add("first_agent_speech_after", False, f"mark {mark_name} never occurred")
        elif not segments:
            add("first_agent_speech_after", False, "no agent speech detected")
        else:
            mark = marks[-1]
            first = segments[0].start
            delta = first - mark
            lo = float(spec.get("min", 0.0))
            hi = float(spec.get("max", 5.0))
            add(
                "first_agent_speech_after",
                lo <= delta <= hi,
                f"delta={delta:.2f}s window=[{lo},{hi}] mark={mark_name} t={mark:.2f}",
            )

    if "agent_speech_after" in expect:
        spec = expect["agent_speech_after"] or {}
        mark_name = str(spec.get("mark", "audio_path_ready"))
        marks = _timeline_marks(timeline, mark_name)
        if not marks:
            add("agent_speech_after", False, f"mark {mark_name} never occurred")
        else:
            mark = marks[-1]
            later = [segment for segment in segments if segment.start >= mark - 0.05]
            if not later:
                add("agent_speech_after", False, f"no agent speech after t={mark:.2f}")
            else:
                delta = later[0].start - mark
                lo = float(spec.get("min", 0.0))
                hi = float(spec.get("max", 5.0))
                add(
                    "agent_speech_after",
                    lo <= delta <= hi,
                    f"delta={delta:.2f}s window=[{lo},{hi}] mark={mark_name} t={mark:.2f}",
                )

    if "message_start_after" in expect:
        spec = expect["message_start_after"]
        marks = _timeline_marks(timeline, str(spec.get("mark", "tone_end")))
        if not marks:
            add("message_start_after", False, f"mark {spec.get('mark')} never occurred")
        else:
            mark = marks[-1]
            after = [s for s in segments if s.start >= mark - 0.05]
            if not after:
                add("message_start_after", False, f"no agent speech after t={mark:.2f}")
            else:
                delta = after[0].start - mark
                lo, hi = float(spec.get("min", 0.0)), float(spec.get("max", 5.0))
                add(
                    "message_start_after",
                    lo <= delta <= hi,
                    f"delta={delta:.2f}s window=[{lo},{hi}] mark_t={mark:.2f}",
                )

    if "max_overlap_with_playback" in expect:
        spec = expect["max_overlap_with_playback"]
        intervals = _playback_intervals(timeline, str(spec["asset"]))
        if not intervals:
            add("max_overlap_with_playback", False, f"asset {spec['asset']} never played")
        else:
            overlap = sum(
                max(0.0, min(s.end, b) - max(s.start, a))
                for a, b in intervals for s in segments
            )
            limit = float(spec.get("max_s", 0.5))
            add(
                "max_overlap_with_playback",
                overlap <= limit,
                f"overlap={overlap:.2f}s max={limit}s playbacks={len(intervals)}",
            )

    if "max_overlap_between_marks" in expect:
        spec = expect["max_overlap_between_marks"] or {}
        start_name = str(spec.get("start", "tone_start"))
        end_name = str(spec.get("end", "tone_end"))
        starts = _timeline_marks(timeline, start_name)
        ends = _timeline_marks(timeline, end_name)
        end = next((mark for mark in ends if starts and mark >= starts[-1]), None)
        if not starts or end is None:
            add(
                "max_overlap_between_marks",
                False,
                f"missing or reversed interval marks start={start_name} end={end_name}",
            )
        else:
            start = starts[-1]
            overlap = sum(
                max(0.0, min(segment.end, end) - max(segment.start, start))
                for segment in segments
            )
            limit = float(spec.get("max_s", 0.05))
            add(
                "max_overlap_between_marks",
                overlap <= limit,
                f"overlap={overlap:.2f}s max={limit}s window=[{start:.2f},{end:.2f}]",
            )

    return results
