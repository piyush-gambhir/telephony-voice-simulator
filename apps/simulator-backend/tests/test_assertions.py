"""Unit tests for the assertion engine (synthetic wavs + timelines)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

from telephony_voice_simulator.assertions import run_checks, speech_segments_from_wav, summarize_checks
from telephony_voice_simulator.machine import SAMPLE_RATE, synth_tone


def _agent_wav(tmp_path: Path, *, speech_at: list[tuple[float, float]], started_at: float = 0.0,
               total: float = 10.0) -> Path:
    """A WAV that is silent except for loud 'speech' in the given windows
    (times are machine-timeline seconds)."""
    samples = np.zeros(int(total * SAMPLE_RATE), dtype=np.int16)
    for start, end in speech_at:
        s = int((start - started_at) * SAMPLE_RATE)
        e = int((end - started_at) * SAMPLE_RATE)
        seg = synth_tone(220, (e - s) / SAMPLE_RATE, amplitude=9000)
        samples[s : s + seg.shape[0]] = seg[: max(0, samples.shape[0] - s)]
    path = tmp_path / "agent-x.wav"
    sf.write(path, samples, SAMPLE_RATE, subtype="PCM_16")
    path.with_suffix(".meta.json").write_text(
        json.dumps({"started_at": started_at, "sample_rate": SAMPLE_RATE})
    )
    return path


def test_vad_finds_segments_with_offset(tmp_path: Path) -> None:
    wav = _agent_wav(tmp_path, speech_at=[(3.0, 4.0), (6.0, 6.6)], started_at=2.0, total=8.0)
    segments = speech_segments_from_wav(wav)
    assert len(segments) == 2
    assert abs(segments[0].start - 3.0) < 0.15
    assert abs(segments[1].start - 6.0) < 0.15


def _base_webhook(reason: str, layer: str = "") -> dict:
    return {
        "event": "call.ended",
        "call_id": "sim-1",
        "data": {"ended_reason": reason, "voicemail_detection_layer": layer},
    }


def test_message_after_tone_passes_and_fails(tmp_path: Path) -> None:
    timeline = [
        {"t": 0.5, "event": "play_start", "asset": "g.wav", "duration": 3.0},
        {"t": 3.5, "event": "play_end", "asset": "g.wav"},
        {"t": 4.5, "event": "tone_start", "freq": 1000.0, "duration": 0.5},
        {"t": 5.0, "event": "tone_end", "freq": 1000.0},
    ]
    good = _agent_wav(tmp_path, speech_at=[(5.6, 8.0)], total=10.0)
    checks = run_checks(
        {"message_start_after": {"mark": "tone_end", "min": 0.1, "max": 2.0}},
        timeline=timeline,
        recordings={"a": good},
        call_ended=_base_webhook("call.ending.voicemail-left-message"),
    )
    assert all(c.passed for c in checks), [c.detail for c in checks]

    early = _agent_wav(tmp_path, speech_at=[(3.8, 6.0)], total=10.0)  # talks pre-beep
    checks = run_checks(
        {"message_start_after": {"mark": "tone_end", "min": 0.1, "max": 2.0}},
        timeline=timeline,
        recordings={"a": early},
        call_ended=_base_webhook("call.ending.voicemail-left-message"),
    )
    assert not all(c.passed for c in checks)


def test_message_after_specific_play_end(tmp_path: Path) -> None:
    timeline = [
        {"t": 1.0, "event": "play_start", "asset": "prompt.wav", "duration": 2.0},
        {"t": 3.0, "event": "play_end", "asset": "prompt.wav"},
        {"t": 3.2, "event": "play_start", "asset": "beep.wav", "duration": 0.4},
        {"t": 3.6, "event": "play_end", "asset": "beep.wav"},
    ]
    good = _agent_wav(tmp_path, speech_at=[(4.0, 6.0)], total=8.0)
    checks = run_checks(
        {"message_start_after": {"mark": "play_end:beep.wav", "min": 0.1, "max": 1.0}},
        timeline=timeline,
        recordings={"a": good},
        call_ended=_base_webhook("call.ending.voicemail-left-message"),
    )
    assert all(c.passed for c in checks), [c.detail for c in checks]


def test_overlap_with_playback(tmp_path: Path) -> None:
    timeline = [
        {"t": 1.0, "event": "play_start", "asset": "g.wav", "duration": 4.0},
        {"t": 5.0, "event": "play_end", "asset": "g.wav"},
    ]
    talker = _agent_wav(tmp_path, speech_at=[(2.0, 4.5)], total=8.0)
    checks = run_checks(
        {"max_overlap_with_playback": {"asset": "g.wav", "max_s": 0.5}},
        timeline=timeline,
        recordings={"a": talker},
        call_ended=None,
    )
    assert not checks[0].passed
    quiet = _agent_wav(tmp_path, speech_at=[(6.0, 7.0)], total=8.0)
    checks = run_checks(
        {"max_overlap_with_playback": {"asset": "g.wav", "max_s": 0.5}},
        timeline=timeline,
        recordings={"a": quiet},
        call_ended=None,
    )
    assert checks[0].passed


def test_overlap_between_tone_marks(tmp_path: Path) -> None:
    timeline = [
        {"t": 4.5, "event": "tone_start", "freq": 1000.0, "duration": 0.5},
        {"t": 5.0, "event": "tone_end", "freq": 1000.0},
    ]
    expectation = {
        "max_overlap_between_marks": {
            "start": "tone_start",
            "end": "tone_end",
            "max_s": 0.05,
        }
    }
    overlap = _agent_wav(tmp_path, speech_at=[(4.6, 5.4)], total=8.0)
    checks = run_checks(
        expectation,
        timeline=timeline,
        recordings={"a": overlap},
        call_ended=None,
    )
    assert not checks[0].passed

    after = _agent_wav(tmp_path, speech_at=[(5.2, 6.0)], total=8.0)
    checks = run_checks(
        expectation,
        timeline=timeline,
        recordings={"a": after},
        call_ended=None,
    )
    assert checks[0].passed


def test_first_agent_speech_after_mark_passes_and_fails(tmp_path: Path) -> None:
    timeline = [{"t": 0.0, "event": "audio_path_ready"}]
    prompt = {"first_agent_speech_after": {"mark": "audio_path_ready", "max": 5.0}}

    good = _agent_wav(tmp_path, speech_at=[(2.6, 3.4)], total=8.0)
    checks = run_checks(
        prompt,
        timeline=timeline,
        recordings={"a": good},
        call_ended=None,
    )
    assert checks[0].passed

    late = _agent_wav(tmp_path, speech_at=[(23.0, 24.0)], total=26.0)
    checks = run_checks(
        prompt,
        timeline=timeline,
        recordings={"a": late},
        call_ended=None,
    )
    assert not checks[0].passed
    assert "delta=23" in checks[0].detail


def test_agent_speech_after_later_transition_ignores_earlier_speech(tmp_path: Path) -> None:
    timeline = [
        {"t": 3.0, "event": "play_end", "asset": "screen.wav"},
        {"t": 12.0, "event": "play_end", "asset": "human.wav"},
    ]
    expectation = {
        "agent_speech_after": {"mark": "play_end:human.wav", "min": 0.0, "max": 4.0}
    }
    agent = _agent_wav(
        tmp_path,
        speech_at=[(4.0, 5.0), (13.0, 14.0)],
        total=16.0,
    )
    checks = run_checks(
        expectation,
        timeline=timeline,
        recordings={"a": agent},
        call_ended=None,
    )
    assert checks[0].passed


def test_webhook_reason_and_layer_checks(tmp_path: Path) -> None:
    wav = _agent_wav(tmp_path, speech_at=[(1.0, 2.0)], total=4.0)
    expect = {
        "webhook_received": True,
        "ended_reason": "call.ending.voicemail-left-message",
        "detection_layer_prefix_any_of": ["semantic", "livekit-amd"],
        "agent_spoke": True,
    }
    checks = run_checks(
        expect,
        timeline=[],
        recordings={"a": wav},
        call_ended=_base_webhook("call.ending.voicemail-left-message", "semantic:voicemail"),
    )
    assert all(c.passed for c in checks), [c.as_dict() for c in checks]


def test_dtmf_checks() -> None:
    timeline = [
        {"t": 4.0, "event": "dtmf_received", "digit": "5"},
        {"t": 12.0, "event": "dtmf_received", "digit": "5"},
    ]
    checks = run_checks(
        {"dtmf_received": {"digit": "5"}, "dtmf_press_count_max": 2},
        timeline=timeline,
        recordings={},
        call_ended=None,
    )
    assert all(c.passed for c in checks)
    checks = run_checks(
        {"dtmf_press_count_max": 1},
        timeline=timeline,
        recordings={},
        call_ended=None,
    )
    assert not checks[0].passed


def test_missing_webhook_fails_expected_reason() -> None:
    checks = run_checks(
        {"webhook_received": True, "ended_reason": "call.ending.voicemail-left-message"},
        timeline=[],
        recordings={},
        call_ended=None,
    )
    assert not all(c.passed for c in checks)


def test_detection_layer_absent() -> None:
    checks = run_checks(
        {"detection_layer_absent": True},
        timeline=[],
        recordings={},
        call_ended=_base_webhook("call.ending.customer-ended-call", ""),
    )
    assert checks[0].passed


def test_frame_zero_click_does_not_merge_into_later_speech(tmp_path: Path) -> None:
    samples = np.zeros(SAMPLE_RATE * 2, dtype=np.int16)
    samples[:960] = 9000
    samples[SAMPLE_RATE:SAMPLE_RATE + 24000] = 9000
    wav = tmp_path / "click.wav"
    sf.write(wav, samples, SAMPLE_RATE, subtype="PCM_16")
    segments = speech_segments_from_wav(wav)
    assert len(segments) == 1
    assert segments[0].start == pytest.approx(1.0)


def test_overlap_counts_repeated_prompts_and_deduplicates_tracks(tmp_path: Path) -> None:
    wav = _agent_wav(tmp_path, speech_at=[(5.1, 5.5)], total=7)
    timeline = [
        {"t": 1, "event": "play_start", "asset": "g.wav"},
        {"t": 2, "event": "play_end", "asset": "g.wav"},
        {"t": 5, "event": "play_start", "asset": "g.wav"},
        {"t": 6, "event": "play_end", "asset": "g.wav"},
    ]
    for limit, passed in ((0.2, False), (0.6, True)):
        checks = run_checks(
            {"max_overlap_with_playback": {"asset": "g.wav", "max_s": limit}},
            timeline=timeline, recordings={"a": wav, "duplicate": wav}, call_ended=None,
        )
        assert checks[0].passed is passed


def test_reversed_marks_and_missing_webhook_are_not_passing_evidence(tmp_path: Path) -> None:
    silence = _agent_wav(tmp_path, speech_at=[], total=3)
    checks = run_checks({
        "max_overlap_between_marks": {"start": "tone_start", "end": "tone_end"},
        "ended_reason_not": ["voicemail"],
        "detection_layer_absent": True,
    }, timeline=[{"t": 2, "event": "tone_start"}, {"t": 1, "event": "tone_end"}],
        recordings={"agent": silence}, call_ended=None)
    assert all(check.passed is False for check in checks)


def test_unavailable_room_check_and_empty_grades_are_indeterminate() -> None:
    checks = run_checks({"message_content": {"expected": "Please call back"}},
                        timeline=[], recordings={}, call_ended=None)
    assert len(checks) == 1
    assert summarize_checks(check.passed for check in checks) is None
    assert summarize_checks([]) is None
    assert summarize_checks([True, None]) is None
    assert summarize_checks([False, None]) is False


def test_explicit_false_webhook_expectations_are_evaluated() -> None:
    checks = run_checks({"webhook_received": False, "detection_layer_absent": False},
                        timeline=[], recordings={}, call_ended=_base_webhook("ended", "semantic"))
    assert {check.name: check.passed for check in checks} == {
        "webhook_received": False, "detection_layer_absent": True,
    }


def test_missing_audio_cannot_pass_silence_or_overlap_assertions() -> None:
    checks = run_checks({
        "agent_spoke": False,
        "max_overlap_with_playback": {"asset": "g.wav", "max_s": 0.5},
    }, timeline=[
        {"t": 0, "event": "play_start", "asset": "g.wav"},
        {"t": 1, "event": "play_end", "asset": "g.wav"},
    ], recordings={}, call_ended=None)
    assert len(checks) == 2
    assert all(check.passed is None for check in checks)


def test_failed_recording_does_not_pass_using_partial_audio(tmp_path: Path) -> None:
    silence = _agent_wav(tmp_path, speech_at=[], total=1)
    checks = run_checks({"agent_spoke": False},
                        timeline=[{"t": 0.5, "event": "agent_track_recording_error"}],
                        recordings={"agent": silence}, call_ended=None)
    assert checks[0].passed is None
