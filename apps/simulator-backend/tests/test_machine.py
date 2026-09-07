"""Unit tests for the SimulatedMachine step engine (no LiveKit required)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from telephony_voice_simulator.machine import SAMPLE_RATE, SimulatedMachine, synth_tone


class BufferAudio:
    """AudioOut that records what was played (and when, per machine clock)."""

    def __init__(self, machine_holder: dict) -> None:
        self.played: list[tuple[str, float]] = []
        self._holder = machine_holder

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        machine = self._holder["m"]
        self.played.append((f"{samples.shape[0] / sample_rate:.2f}s", machine.now()))
        await asyncio.sleep(samples.shape[0] / sample_rate)


def _machine(spec: dict, tmp_path: Path) -> tuple[SimulatedMachine, BufferAudio]:
    holder: dict = {}
    audio = BufferAudio(holder)
    machine = SimulatedMachine(spec, assets_dir=tmp_path, audio_out=audio)
    holder["m"] = machine
    return machine, audio


def _events(machine: SimulatedMachine, name: str) -> list[dict]:
    return [e.as_dict() for e in machine.timeline if e.event == name]


async def test_tone_and_hold_timeline(tmp_path: Path) -> None:
    machine, audio = _machine(
        {"main": [{"wait": 0.05}, {"tone": {"freq": 1000, "duration": 0.1}}, {"hold": 0.05}]},
        tmp_path,
    )
    await machine.run()
    assert len(_events(machine, "tone_start")) == 1
    assert len(_events(machine, "tone_end")) == 1
    tone_end = _events(machine, "tone_end")[0]["t"]
    assert tone_end >= 0.15 - 0.02
    assert len(audio.played) == 1


async def test_play_asset_marks_start_end(tmp_path: Path) -> None:
    import soundfile as sf

    wav = tmp_path / "greeting.wav"
    sf.write(wav, synth_tone(300, 0.1), SAMPLE_RATE, subtype="PCM_16")
    machine, _ = _machine({"main": [{"play": "greeting.wav"}]}, tmp_path)
    await machine.run()
    assert _events(machine, "play_start")[0]["asset"] == "greeting.wav"
    assert _events(machine, "play_end")


async def test_dtmf_switch_interrupts_wait(tmp_path: Path) -> None:
    machine, _ = _machine(
        {
            "main": [{"wait": 5.0}],
            "on_dtmf": {"digits": ["5"], "switch": "connected"},
            "connected": [{"hold": 0.05}],
        },
        tmp_path,
    )
    task = asyncio.create_task(machine.run())
    await asyncio.sleep(0.05)
    machine.on_dtmf("5")
    await asyncio.wait_for(task, timeout=2.0)
    assert _events(machine, "dtmf_received")[0]["digit"] == "5"
    assert _events(machine, "sequence_switch")[0]["to"] == "connected"
    # The 5s wait was interrupted well before its full duration.
    assert machine.timeline[-1].t < 2.0


async def test_dtmf_ignored_mode(tmp_path: Path) -> None:
    machine, _ = _machine({"main": [{"wait": 0.05}], "on_dtmf": "ignore"}, tmp_path)
    task = asyncio.create_task(machine.run())
    machine.on_dtmf("5")
    await asyncio.wait_for(task, timeout=2.0)
    assert _events(machine, "dtmf_ignored")
    assert not _events(machine, "sequence_switch")


async def test_dtmf_wrong_digit_rejected(tmp_path: Path) -> None:
    machine, _ = _machine(
        {"main": [{"wait": 0.05}], "on_dtmf": {"digits": ["5"], "switch": "connected"},
         "connected": [{"hold": 0.01}]},
        tmp_path,
    )
    task = asyncio.create_task(machine.run())
    machine.on_dtmf("9")
    await asyncio.wait_for(task, timeout=2.0)
    assert _events(machine, "dtmf_rejected")
    assert not _events(machine, "sequence_switch")


async def test_repeat_from_loops_until_dtmf(tmp_path: Path) -> None:
    machine, _ = _machine(
        {
            "main": [{"wait": 0.01}, {"tone": {"freq": 800, "duration": 0.02}},
                     {"wait": 0.02}, {"repeat_from": 1}],
            "on_dtmf": {"switch": "connected"},
            "connected": [{"hold": 0.01}],
        },
        tmp_path,
    )
    task = asyncio.create_task(machine.run())
    await asyncio.sleep(0.2)  # let the prompt loop a few times
    machine.on_dtmf("1")
    await asyncio.wait_for(task, timeout=2.0)
    assert len(_events(machine, "tone_start")) >= 2  # it re-prompted


async def test_hangup_sets_event(tmp_path: Path) -> None:
    machine, _ = _machine({"main": [{"hangup": True}]}, tmp_path)
    await machine.run()
    assert machine.hangup_requested.is_set()
    assert _events(machine, "machine_hangup")


async def test_unknown_step_raises(tmp_path: Path) -> None:
    machine, _ = _machine({"main": [{"frobnicate": 1}]}, tmp_path)
    with pytest.raises(ValueError):
        await machine.run()


async def test_max_duration_forces_termination(tmp_path: Path) -> None:
    # A script that would otherwise hang forever (a 1-hour hold) must be
    # capped: the machine hangs up at max_duration. This is the universal
    # "no scenario runs infinitely" guarantee.
    machine, _ = _machine(
        {"main": [{"hold": 3600}], "max_duration": 0.05}, tmp_path
    )
    await machine.run()
    assert machine.hangup_requested.is_set()
    assert _events(machine, "machine_timeout")


async def test_normal_scenario_does_not_hit_cap(tmp_path: Path) -> None:
    machine, _ = _machine(
        {"main": [{"wait": 0.02}, {"hangup": True}], "max_duration": 5.0}, tmp_path
    )
    await machine.run()
    assert _events(machine, "machine_hangup")
    assert not _events(machine, "machine_timeout")


def test_synth_tone_shape() -> None:
    tone = synth_tone(1000, 0.5, 12000)
    assert tone.dtype == np.int16
    assert abs(tone.shape[0] - SAMPLE_RATE // 2) <= 1
    assert np.abs(tone).max() <= 12000


async def test_dtmf_interrupts_audio_without_claiming_completed_beep(tmp_path: Path) -> None:
    machine, _ = _machine({
        "main": [{"tone": {"duration": 5}}],
        "on_dtmf": {"digits": ["5"], "switch": "connected"},
        "connected": [{"hangup": True}],
    }, tmp_path)
    task = asyncio.create_task(machine.run())
    await asyncio.sleep(0.02)
    machine.on_dtmf("5")
    await asyncio.wait_for(task, timeout=0.5)
    assert _events(machine, "tone_interrupted")
    assert not _events(machine, "tone_end")
    assert _events(machine, "machine_hangup")


async def test_run_preserves_transport_clock(tmp_path: Path, monkeypatch) -> None:
    now = [10.0]
    monkeypatch.setattr("telephony_voice_simulator.machine.time.monotonic", lambda: now[0])
    machine, _ = _machine({"main": [{"hangup": True}]}, tmp_path)
    machine.start_clock()
    machine.mark("agent_track_recording_start")
    now[0] = 12.0
    machine.mark("audio_path_ready")
    await machine.run()
    assert _events(machine, "machine_hangup")[0]["t"] == 2.0


async def test_audio_transport_failure_propagates(tmp_path: Path) -> None:
    class FailingAudio:
        async def play(self, samples, sample_rate):
            raise RuntimeError("transport disconnected")

    machine = SimulatedMachine({"main": [{"tone": {}}]}, assets_dir=tmp_path, audio_out=FailingAudio())
    with pytest.raises(RuntimeError, match="transport disconnected"):
        await machine.run()
    assert not _events(machine, "tone_end")


async def test_dtmf_before_media_ready_skips_the_gate(tmp_path: Path) -> None:
    machine, _ = _machine({
        "main": [{"wait": 45}],
        "on_dtmf": {"digits": ["5"], "switch": "connected"},
        "connected": [{"hangup": True}],
    }, tmp_path)
    machine.on_dtmf("5")
    await asyncio.wait_for(machine.run(), timeout=0.5)
    assert _events(machine, "sequence_start")[0]["sequence"] == "connected"
    assert _events(machine, "machine_hangup")


async def test_timeout_marks_incomplete_playback(tmp_path: Path) -> None:
    import soundfile as sf

    sf.write(tmp_path / "long.wav", synth_tone(300, 2), SAMPLE_RATE, subtype="PCM_16")
    machine, _ = _machine({"main": [{"play": "long.wav"}], "max_duration": 0.02}, tmp_path)
    await machine.run()
    assert _events(machine, "play_interrupted")
    assert not _events(machine, "play_end")
    assert _events(machine, "machine_timeout")
