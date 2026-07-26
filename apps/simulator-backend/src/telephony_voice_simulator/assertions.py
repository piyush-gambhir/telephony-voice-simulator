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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

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
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


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
        elif start is not None and (i - (last_voiced or i)) * VAD_FRAME_S > VAD_MERGE_GAP_S:
            segments.append(
                SpeechSegment(offset + start * VAD_FRAME_S, offset + ((last_voiced or i) + 1) * VAD_FRAME_S)
            )
            start = None
            last_voiced = None
    if start is not None:
        segments.append(
            SpeechSegment(offset + start * VAD_FRAME_S, offset + ((last_voiced or start) + 1) * VAD_FRAME_S)
        )
    return [s for s in segments if s.duration >= VAD_MIN_SEGMENT_S]


def merged_agent_segments(recordings: dict[str, Path]) -> list[SpeechSegment]:
    segments: list[SpeechSegment] = []
    for path in recordings.values():
        if path.exists():
            segments.extend(speech_segments_from_wav(path))
    return sorted(segments, key=lambda s: s.start)


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


def _playback_interval(timeline: list[dict], asset: str) -> tuple[float, float] | None:
    starts = _timeline_marks(timeline, "play_start", asset=asset)
    ends = _timeline_marks(timeline, "play_end", asset=asset)
    if starts and ends:
        return starts[0], ends[0]
    return None


def run_checks(
    expect: dict[str, Any],
    *,
    timeline: list[dict],
    recordings: dict[str, Path],
    call_ended: dict[str, Any] | None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    segments = merged_agent_segments(recordings)
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

    def add(name: str, passed: bool, detail: str) -> None:
        results.append(CheckResult(name, passed, detail))

    if expect.get("webhook_received"):
        add("webhook_received", call_ended is not None, f"call.ended={'yes' if call_ended else 'MISSING'}")

    if "ended_reason" in expect:
        want = str(expect["ended_reason"])
        add("ended_reason", ended_reason == want, f"want={want} got={ended_reason or '(none)'}")
    if "ended_reason_any_of" in expect:
        want = [str(w) for w in expect["ended_reason_any_of"]]
        add("ended_reason_any_of", ended_reason in want, f"want∈{want} got={ended_reason or '(none)'}")
    if "ended_reason_not" in expect:
        banned = [str(w) for w in expect["ended_reason_not"]]
        add("ended_reason_not", ended_reason not in banned, f"banned={banned} got={ended_reason or '(none)'}")

    if "detection_layer_prefix_any_of" in expect:
        prefixes = [str(p) for p in expect["detection_layer_prefix_any_of"]]
        ok = any(layer.startswith(p) for p in prefixes)
        add("detection_layer", ok, f"want prefix∈{prefixes} got={layer or '(none)'}")
    if expect.get("detection_layer_absent"):
        add("detection_layer_absent", not layer, f"got={layer or '(none)'}")

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
        interval = _playback_interval(timeline, str(spec["asset"]))
        if interval is None:
            add("max_overlap_with_playback", False, f"asset {spec['asset']} never played")
        else:
            a, b = interval
            overlap = sum(
                max(0.0, min(s.end, b) - max(s.start, a)) for s in segments
            )
            limit = float(spec.get("max_s", 0.5))
            add(
                "max_overlap_with_playback",
                overlap <= limit,
                f"overlap={overlap:.2f}s max={limit}s window=[{a:.2f},{b:.2f}]",
            )

    if "max_overlap_between_marks" in expect:
        spec = expect["max_overlap_between_marks"] or {}
        start_name = str(spec.get("start", "tone_start"))
        end_name = str(spec.get("end", "tone_end"))
        starts = _timeline_marks(timeline, start_name)
        ends = _timeline_marks(timeline, end_name)
        if not starts or not ends:
            add(
                "max_overlap_between_marks",
                False,
                f"missing interval marks start={start_name} end={end_name}",
            )
        else:
            start = starts[-1]
            end = next((mark for mark in ends if mark >= start), ends[-1])
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
