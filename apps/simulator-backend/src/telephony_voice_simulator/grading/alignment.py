"""Grade phone-path timing from the dual-channel full-call recording.

The recording holds the far party (the agent under test) on one channel and this
simulator's playback on the other, on one clock. Each segment the callee played is found
on the playback channel by cross-correlating its compiled audio near the time its TwiML
was served, which places the compiled marks (``tone_start``, ``tone_end``,
``play_start:<asset>``, ``play_end:<asset>``) on that clock. Agent speech comes from the
other channel through the same energy VAD as the room-level runner. The playback channel
is whichever one carries the compiled audio, so grading does not depend on the provider's
channel order.

A mark counts only when the playback channel carries the segment's audio up to it, so a
prompt cut short by a keypress or a hangup cannot vouch for marks it never reached. A
segment served after the recording ended never played, and its marks never occurred.

Times are measured at the callee, where a real mailbox records, so network delay counts
the way it would for a real mailbox. When the recording began after the first playback
did, speech before it is unseen: a check passes only if it would pass whatever the agent
did in that gap, and fails only on what was seen.

The checks mean what they mean in ``checks.run_checks``, with one stricter rule:
``message_start_after`` takes the first speech still under way after the mark, so a
message that runs through the beep fails even when it pauses afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .checks import SpeechSegment, speech_segments_from_wav

TIMING_CHECKS = frozenset({
    "message_start_after", "agent_speech_after", "first_agent_speech_after",
    "max_overlap_with_playback", "max_overlap_between_marks",
})
MATCH_MIN = 0.6
MATCH_WINDOW_S = 0.05
SILENT_RMS = 100.0
MARK_TOLERANCE_S = 0.05
SEARCH_BEFORE_S = 2.0
SEARCH_AFTER_S = 4.0
NEXT_SERVE_MARGIN_S = 0.25
UNHINTED_SEARCH_S = 120.0
PLAYED_BY_END_S = 1.0
SOURCE = "source=full-call"

Row = tuple[bool | None, str]
Playback = tuple[float, float, float, float]


@dataclass
class Alignment:
    """Marks, playback intervals and agent speech on the full-call recording's clock.

    ``marks`` keeps every played occurrence in execution order, with None for one the
    playback channel could not confirm. Each ``playbacks`` occurrence is ``(start,
    confirmed_end, possible_end, unplaced_s)``: the asset surely played until
    ``confirmed_end``, may have played on until ``possible_end``, and ``unplaced_s`` is
    playback that could not be placed on the clock at all. ``blind_s`` is how long the
    first playback ran before the recording began (infinite when unknown).
    """

    marks: list[tuple[str, float | None]] = field(default_factory=list)
    playbacks: dict[str, list[Playback]] = field(default_factory=dict)
    modeled: set[str] = field(default_factory=set)
    speech: list[SpeechSegment] = field(default_factory=list)
    located: int = 0
    audible: int = 0
    blind_s: float = float("inf")
    playback_channel: int = 1

    def resolve(self, name: str) -> float | Row:
        """The last occurrence of a mark, or the row to report when it has no usable time."""
        times = [t for mark, t in self.marks if mark == name]
        if not times:
            if name in self.modeled:
                return False, f"mark {name} never played {SOURCE}"
            return None, f"mark {name} is not observable on the phone path"
        if times[-1] is None:
            return None, f"mark {name} not confirmed on the playback channel {SOURCE}"
        if self.blind_s > MARK_TOLERANCE_S and times[-1] < MARK_TOLERANCE_S:
            return None, f"mark {name} precedes the recording {SOURCE}"
        return times[-1]


@dataclass
class _Placed:
    marks: dict[str, float]
    start: int = 0
    heard: int = 0
    end: int = 0
    found: bool = False
    doubtful: bool = False


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


def _similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b)) / denominator if denominator else 0.0


def _load_template(audio_dir: Path, name: str, rate: int) -> np.ndarray | None:
    import soundfile as sf

    path = audio_dir / name
    if Path(name).name != name or not path.is_file():
        return None
    data, source_rate = sf.read(path, dtype="int16", always_2d=True)
    samples = data[:, 0].astype(np.float64)
    if source_rate != rate and samples.size:
        count = max(1, round(samples.size * rate / source_rate))
        samples = np.interp(np.linspace(0, samples.size - 1, count),
                            np.arange(samples.size), samples)
    return samples


def _audible(template: np.ndarray, step: int) -> bool:
    return any(_rms(template[i:i + step]) >= SILENT_RMS for i in range(0, len(template), step))


def _played(playback: np.ndarray, template: np.ndarray, start: int,
            step: int) -> tuple[int, bool, bool]:
    """How far the audible template plays from ``start``, whether it stopped, and whether
    what followed is in doubt.

    Audible windows are compared in order; the first one that does not match, or that the
    recording no longer covers, ends the confirmed playback. A real cut leaves the channel
    silent until the next segment, so a mismatch with sound right after it, or with the
    template matching again later, leaves the rest in doubt instead. Silent windows say
    nothing, so a playback that is never cut may also have played its silent tail.
    """
    extent = 0
    for lo in range(0, len(template), step):
        hi = min(len(template), lo + step)
        window = template[lo:hi]
        if start + lo < 0 or _rms(window) < SILENT_RMS:
            continue
        if start + hi > len(playback):
            return extent, True, False
        if _similarity(window, playback[start + lo:start + hi]) < MATCH_MIN:
            after = playback[start + hi:start + hi + step]
            return extent, True, _rms(after) >= SILENT_RMS or _resumes(
                playback, template, start, hi, step)
        extent = hi
    return extent, False, False


def _resumes(playback: np.ndarray, template: np.ndarray, start: int, offset: int,
             step: int) -> bool:
    """True when a later audible window of the template still matches at the same start."""
    for lo in range(offset, len(template), step):
        hi = min(len(template), lo + step)
        window = template[lo:hi]
        if start + hi > len(playback):
            return False
        if _rms(window) >= SILENT_RMS and _similarity(
                window, playback[start + lo:start + hi]) >= MATCH_MIN:
            return True
    return False


def _locate(playback: np.ndarray, template: np.ndarray, first: int, last: int) -> int | None:
    """Start in ``[first, last]`` where the template correlates best with the channel."""
    size = len(template)
    first, last = max(first, 1 - size), min(last, len(playback) - 1)
    if last < first:
        return None
    region = np.zeros(last - first + size)
    lo, hi = max(first, 0), min(last + size, len(playback))
    region[lo - first:hi - first] = playback[lo:hi]
    length = 1 << (len(region) + size).bit_length()
    spectrum = np.fft.rfft(region, length) * np.conj(np.fft.rfft(template, length))
    correlation = np.fft.irfft(spectrum, length)[: last - first + 1]
    return first + int(np.argmax(correlation))


def _place(playback: np.ndarray, rate: int, emitted: list[dict[str, Any]], compiled: Any,
           audio_dir: Path) -> tuple[list[_Placed], int, int, float]:
    """Find each emitted segment on one channel, in execution order.

    Each search covers a few seconds around when the segment's TwiML was served, moved by
    the offset the previous match measured, and ends where the next segment's TwiML was
    served, since a segment starts playing before the next one is requested. So repeated
    prompts cannot swap places. The next playback starts no earlier than the audible end
    of the previous one, and a silent tail counts as played only up to where the next
    playback starts.
    """
    step = max(1, int(MATCH_WINDOW_S * rate))
    templates: dict[str, np.ndarray | None] = {}
    placed: list[_Placed] = []
    audible = located = 0
    cursor: int | None = None
    offset = 0.0
    blind = float("inf")
    first_audible = True
    hints = [entry.get("t") if isinstance(entry.get("t"), int | float) else None
             for entry in emitted]
    for position, entry in enumerate(emitted):
        try:
            segment = compiled.sequences[entry["seq"]][int(entry["index"])]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        hint = hints[position]
        following = next((h for h in hints[position + 1:] if h is not None), None)
        item = _Placed(marks=dict(segment.marks))
        template = None
        if segment.audio_file:
            if segment.audio_file not in templates:
                templates[segment.audio_file] = _load_template(audio_dir, segment.audio_file, rate)
            template = templates[segment.audio_file]
        if template is not None and _audible(template, step):
            audible += 1
            floor = -len(template) if cursor is None else cursor
            if hint is None:
                window = (floor, floor + int(UNHINTED_SEARCH_S * rate))
            else:
                guess = (hint + offset) * rate
                latest = guess + SEARCH_AFTER_S * rate
                if following is not None:
                    latest = min(latest, (following + offset + NEXT_SERVE_MARGIN_S) * rate)
                window = (max(floor, int(guess - SEARCH_BEFORE_S * rate)), int(latest))
            start = _locate(playback, template, *window)
            extent, cut, doubtful = (_played(playback, template, start, step)
                                     if start is not None else (0, True, False))
            if extent:
                located += 1
                item.start, item.found, item.heard = start, True, start + extent
                item.end = start + (extent if cut else len(template))
                item.doubtful = doubtful
                cursor = max(cursor or 0, start + extent - int(MARK_TOLERANCE_S * rate))
                if hint is not None:
                    offset = start / rate - hint
                if first_audible:
                    blind = max(0.0, -start / rate)
            elif hint is not None and hint + offset > len(playback) / rate - PLAYED_BY_END_S:
                continue
            first_audible = False
        placed.append(item)
    found = [item for item in placed if item.found]
    for item, following in zip(found, [*found[1:], None]):
        limit = following.start if following else len(playback)
        item.end = min(item.end, max(limit, item.heard))
    return placed, audible, located, blind


def align(recording: Path, emitted: list[dict[str, Any]], compiled: Any,
          audio_dir: Path) -> Alignment:
    """Place every emitted segment's marks on the full-call recording's clock.

    Raises ValueError when the recording cannot anchor any played segment.
    """
    import soundfile as sf

    data, rate = sf.read(recording, dtype="int16", always_2d=True)
    if data.shape[1] < 2:
        raise ValueError(f"full-call recording has {data.shape[1]} channel; need 2")
    if not emitted:
        raise ValueError("no emitted segments to align")
    for channel in (1, 0):
        playback = data[:, channel].astype(np.float64)
        placed, audible, located, blind = _place(playback, rate, emitted, compiled, audio_dir)
        if located:
            break
    else:
        raise ValueError(f"no played segment found on either channel (audible={audible})")
    result = Alignment(
        speech=speech_segments_from_wav(recording, channel=1 - channel),
        modeled={mark for sequence in compiled.sequences.values()
                 for segment in sequence for mark in segment.marks},
        located=located, audible=audible, blind_s=blind, playback_channel=channel,
    )
    for item in placed:
        played = item.end - item.start
        for name, offset in item.marks.items():
            at = offset * rate
            result.marks.append((name, (item.start + at) / rate
                                 if item.found and at <= played + 1 else None))
        for name, offset in item.marks.items():
            if not name.startswith("play_end:"):
                continue
            asset = name.removeprefix("play_end:")
            begin = item.marks.get(f"play_start:{asset}")
            occurrences = result.playbacks.setdefault(asset, [])
            if begin is None:
                occurrences.append((0.0, 0.0, 0.0, float("inf")))
            elif not item.found:
                occurrences.append((0.0, 0.0, 0.0, offset - begin))
            elif begin * rate < played or item.doubtful:
                first = item.start / rate + begin
                confirmed = item.start / rate + max(begin, min(offset, played / rate))
                possible = item.start / rate + offset if item.doubtful else confirmed
                occurrences.append((first, confirmed, possible, 0.0))
    return result


def _overlap(speech: list[SpeechSegment], start: float, end: float) -> float:
    return sum(max(0.0, min(s.end, end) - max(s.start, start)) for s in speech)


def _overlap_row(speech: list[SpeechSegment], occurrences: list[Playback],
                 limit: float, detail: str) -> Row:
    """Fail on overlap seen during confirmed playback; pass only if the worst case fits.

    The worst case adds speech during playback that may have continued, all playback
    before the recording began, and playback that could not be placed at all.
    """
    seen = sum(_overlap(speech, max(a, 0.0), b) for a, b, _, _ in occurrences)
    unseen = sum(max(0.0, min(c, 0.0) - a) for a, _, c, _ in occurrences)
    unsure = sum(_overlap(speech, max(b, 0.0), c) + extra for _, b, c, extra in occurrences)
    text = f"overlap={seen:.2f}s max={limit}s {detail}"
    if unseen:
        text += f" unseen_before_recording={unseen:.2f}s"
    if unsure:
        text += f" unconfirmed_playback_overlap<={unsure:.2f}s"
    if seen > limit:
        return False, f"{text} {SOURCE}"
    return (True if seen + unseen + unsure <= limit else None), f"{text} {SOURCE}"


def grade(expect: dict[str, Any], alignment: Alignment) -> dict[str, Row]:
    """Timing check rows keyed by check name, for the timing checks the scenario expects."""
    rows: dict[str, Row] = {}
    speech = alignment.speech
    blind = alignment.blind_s > MARK_TOLERANCE_S
    for name in sorted(TIMING_CHECKS & expect.keys()):
        spec = expect[name] or {}
        if name == "max_overlap_with_playback":
            asset = str(spec["asset"])
            occurrences = alignment.playbacks.get(asset, [])
            if not occurrences:
                rows[name] = ((False, f"asset {asset} never played {SOURCE}")
                              if f"play_end:{asset}" in alignment.modeled
                              else (None, f"playback of {asset} is not observable on the phone path"))
            else:
                rows[name] = _overlap_row(speech, occurrences, float(spec.get("max_s", 0.5)),
                                          f"playbacks={len(occurrences)}")
            continue
        if name == "max_overlap_between_marks":
            start_name = str(spec.get("start", "tone_start"))
            end_name = str(spec.get("end", "tone_end"))
            start = alignment.resolve(start_name)
            if isinstance(start, tuple):
                rows[name] = start
                continue
            ends = [t for mark, t in alignment.marks if mark == end_name]
            end = next((t for t in ends if t is not None and t >= start), None)
            if end is None:
                rows[name] = ((None, f"mark {end_name} not confirmed on the playback channel {SOURCE}")
                              if None in ends or end_name not in alignment.modeled
                              else (False, f"missing or reversed interval marks start={start_name} "
                                           f"end={end_name} {SOURCE}"))
                continue
            rows[name] = _overlap_row(speech, [(start, end, end, 0.0)],
                                      float(spec.get("max_s", 0.05)),
                                      f"window=[{start:.2f},{end:.2f}]")
            continue
        mark_name = str(spec.get("mark", "tone_end" if name == "message_start_after"
                                 else "audio_path_ready"))
        mark = alignment.resolve(mark_name)
        if isinstance(mark, tuple):
            rows[name] = mark
            continue
        lo, hi = float(spec.get("min", 0.0)), float(spec.get("max", 5.0))
        if name == "first_agent_speech_after":
            first = speech[0] if speech else None
        elif name == "message_start_after":
            first = next((s for s in speech if s.end > mark + MARK_TOLERANCE_S), None)
        else:
            first = next((s for s in speech if s.start >= mark - MARK_TOLERANCE_S), None)
        over = " talking_over_mark" if any(
            s.start < mark - MARK_TOLERANCE_S < s.end for s in speech) else ""
        unseen = name == "first_agent_speech_after" and blind
        if first is None:
            rows[name] = (None if unseen else False,
                          f"no agent speech after {mark_name} t={mark:.2f}s{over} {SOURCE}")
            continue
        delta = first.start - mark
        passed = lo <= delta <= hi
        rows[name] = (
            None if unseen and delta >= lo else passed,
            f"delay_after_mark={delta:.2f}s window=[{lo},{hi}] mark={mark_name} t={mark:.2f}s"
            f"{over}{' unseen_before_recording' if unseen else ''} {SOURCE}",
        )
    return rows
