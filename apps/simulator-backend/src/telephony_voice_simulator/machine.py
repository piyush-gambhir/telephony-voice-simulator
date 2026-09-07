"""The simulated answering machine / connect-gate / screener / human.

A :class:`SimulatedMachine` executes a scenario's scripted step sequences and
emits audio through an injected :class:`AudioOut`, so the same engine drives a
real LiveKit participant (see ``bot.py``) and fast in-process unit tests.

Everything the machine does is stamped into ``timeline`` (seconds since
``run()`` started). The assertion layer replays that timeline against the
agent's recorded audio and the captured ``call.ended`` webhook — e.g. "the
configured voicemail message must start 0.2-2.0s after ``tone_end``".

Step vocabulary (YAML ``machine:`` block):

    main:                        # every scenario starts in ``main``
      - wait: 0.8                # seconds of silence
      - play: greeting_stock.wav # a corpus asset (48k mono wav)
      - tone: {freq: 1000, duration: 0.5, amplitude: 12000}
      - repeat_from: 1           # loop back to step index 1 (re-prompt gates)
      - hangup: true             # machine ends the call from its side
      - hold: 120                # stay silent on the line (e.g. after the beep,
                                 # "recording" whatever the agent says)
    on_dtmf:                     # optional: how a keypress is handled
      digits: ["5"]              # accepted digits (omit = any)
      switch: connected          # jump to this sequence (or "ignore")
    connected:                   # any number of named sequences
      - play: human_hello.wav
      - hold: 60

``on_dtmf: ignore`` (string form) models a broken relay / gate that ignores
input — the machine just keeps executing its current sequence.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .scenario_validation import asset_path, validate_machine

SAMPLE_RATE = 48000

# Hard ceiling (seconds) on any single machine run — the universal
# guarantee that a simulated call always terminates. Covers the longest
# greeting (~20s) + a full mailbox record window (~45s) + a screener
# transfer, with headroom. Scenario ``max_duration`` overrides it.
DEFAULT_MAX_DURATION = 100.0


class AudioOut(Protocol):
    """Where the machine's audio goes (LiveKit track in prod, a buffer in tests)."""

    async def play(self, samples: np.ndarray, sample_rate: int) -> None: ...


def synth_tone(
    freq_hz: float,
    duration_s: float,
    amplitude: int = 12000,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """A record-beep-like sine burst with 10ms fade in/out (no clicks)."""
    n = max(1, int(duration_s * sample_rate))
    t = np.arange(n) / float(sample_rate)
    wave = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    fade = min(int(0.01 * sample_rate), n // 2)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave.astype(np.int16)


@dataclass
class TimelineEvent:
    t: float
    event: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"t": round(self.t, 3), "event": self.event, **self.detail}


class SimulatedMachine:
    """Executes scenario step sequences and reacts to DTMF / hangup events."""

    def __init__(
        self,
        machine_spec: dict[str, Any],
        *,
        assets_dir: Path,
        audio_out: AudioOut,
    ) -> None:
        self._spec = machine_spec
        self._assets_dir = assets_dir
        self._audio = audio_out
        self.timeline: list[TimelineEvent] = []
        self.hangup_requested = asyncio.Event()
        self._switch_to: str | None = None
        self._interrupt = asyncio.Event()
        self._started_at: float | None = None
        self._dtmf_spec = machine_spec.get("on_dtmf")
        # Hard ceiling on how long this machine can stay on the line, so NO
        # scenario can ever hang forever (a real mailbox / gate / screener all
        # end the call on their own). Scenario-overridable via ``max_duration``;
        # the default is generous enough for the longest greeting + record
        # window + a transfer. When it fires the machine hangs up, exactly as a
        # carrier would after its own max-call / max-record timeout.
        self._max_duration = float(machine_spec.get("max_duration", DEFAULT_MAX_DURATION))

    # ------------------------------------------------------------------ events

    def now(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    def start_clock(self) -> None:
        """Establish the shared clock before a transport begins recording."""
        if self._started_at is None:
            self._started_at = time.monotonic()

    def bind_audio(self, audio: AudioOut) -> None:
        self._audio = audio

    def mark(self, event: str, **detail: Any) -> None:
        self.timeline.append(TimelineEvent(self.now(), event, detail))

    def on_dtmf(self, digit: str) -> None:
        """Called by the transport when the agent presses a key."""
        self.mark("dtmf_received", digit=digit)
        spec = self._dtmf_spec
        if spec is None or spec == "ignore":
            self.mark("dtmf_ignored", digit=digit)
            return
        accepted = spec.get("digits")
        if accepted and digit not in [str(d) for d in accepted]:
            self.mark("dtmf_rejected", digit=digit)
            return
        target = spec.get("switch")
        if target:
            self._switch_to = str(target)
            self._interrupt.set()

    # ------------------------------------------------------------------- steps

    def _load_asset(self, name: str) -> np.ndarray:
        import soundfile as sf

        path = asset_path(self._assets_dir, name)
        if not path.is_file():
            raise FileNotFoundError(
                f"corpus asset {name!r} is missing from {self._assets_dir}. "
                "Generate the private corpus or set SIMULATOR_CORPUS_ASSETS_DIR "
                "to an approved corpus mount."
            )
        data, rate = sf.read(path, dtype="int16", always_2d=True)
        samples = data[:, 0]
        if not samples.size:
            raise ValueError(f"corpus asset {name!r} contains no audio")
        if rate != SAMPLE_RATE:
            # Linear resample — corpus assets are built at 48k, this is a
            # convenience for ad-hoc/imported files only.
            duration = samples.shape[0] / float(rate)
            target_n = max(1, int(duration * SAMPLE_RATE))
            x_old = np.linspace(0.0, duration, samples.shape[0])
            x_new = np.linspace(0.0, duration, target_n)
            samples = np.interp(x_new, x_old, samples.astype(np.float64)).astype(np.int16)
        return samples

    async def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep; True if interrupted by a sequence switch."""
        try:
            await asyncio.wait_for(self._interrupt.wait(), timeout=max(0.0, seconds))
            return True
        except asyncio.TimeoutError:
            return False

    async def _play(self, samples: np.ndarray) -> bool:
        """Play until completion or DTMF; always join both child tasks."""
        playback = asyncio.create_task(self._audio.play(samples, SAMPLE_RATE))
        interrupted = asyncio.create_task(self._interrupt.wait())
        try:
            done, _ = await asyncio.wait(
                (playback, interrupted), return_when=asyncio.FIRST_COMPLETED
            )
            if playback in done:
                await playback  # propagate transport failures
                return True
            return False
        finally:
            for task in (playback, interrupted):
                task.cancel()
            await asyncio.gather(playback, interrupted, return_exceptions=True)

    async def _run_step(self, step: dict[str, Any], index: int) -> int | None:
        """Execute one step; returns the next index (None = advance)."""
        if "wait" in step:
            self.mark("wait_start", seconds=float(step["wait"]))
            await self._interruptible_sleep(float(step["wait"]))
            return None
        if "hold" in step:
            self.mark("hold_start", seconds=float(step["hold"]))
            await self._interruptible_sleep(float(step["hold"]))
            self.mark("hold_end")
            return None
        if "play" in step:
            name = str(step["play"])
            samples = self._load_asset(name)
            duration = samples.shape[0] / float(SAMPLE_RATE)
            self.mark("play_start", asset=name, duration=round(duration, 3))
            try:
                completed = await self._play(samples)
            except BaseException:
                self.mark("play_interrupted", asset=name)
                raise
            self.mark("play_end" if completed else "play_interrupted", asset=name)
            return None
        if "tone" in step:
            spec = step["tone"] or {}
            freq = float(spec.get("freq", 1000.0))
            duration = float(spec.get("duration", 0.5))
            amplitude = int(spec.get("amplitude", 12000))
            samples = synth_tone(freq, duration, amplitude)
            self.mark("tone_start", freq=freq, duration=duration)
            try:
                completed = await self._play(samples)
            except BaseException:
                self.mark("tone_interrupted", freq=freq)
                raise
            self.mark("tone_end" if completed else "tone_interrupted", freq=freq)
            return None
        if "repeat_from" in step:
            # Yield even when the preceding steps complete synchronously, so
            # cancellation and the hard deadline can always run.
            await asyncio.sleep(0)
            return int(step["repeat_from"])
        if "hangup" in step:
            self.mark("machine_hangup")
            self.hangup_requested.set()
            return -1  # sentinel: stop the sequence
        raise ValueError(f"unknown machine step at index {index}: {step!r}")

    async def _run_sequence(self, name: str) -> None:
        steps: list[dict[str, Any]] = list(self._spec.get(name) or [])
        self.mark("sequence_start", sequence=name)
        i = 0
        while i < len(steps):
            if self._interrupt.is_set():
                return
            nxt = await self._run_step(steps[i], i)
            if nxt == -1:
                return
            i = i + 1 if nxt is None else nxt
        self.mark("sequence_end", sequence=name)

    async def _run_scripted(self) -> None:
        """Run ``main``, following ``on_dtmf`` switches, until done/hangup."""
        sequence = "main"
        while True:
            if self._switch_to:
                sequence, self._switch_to = self._switch_to, None
                self.mark("sequence_switch", to=sequence)
            self._interrupt.clear()
            await self._run_sequence(sequence)
            if self.hangup_requested.is_set():
                return
            if self._switch_to:
                continue
            return  # main ran to completion with no switch

    async def run(self) -> None:
        """Execute the scenario, bounded by the hard ``max_duration`` cap.

        The cap is the universal guarantee that a call always ends: if the
        script somehow keeps the line open past the ceiling (a ``hold`` longer
        than the cap, a re-prompt loop the agent never clears, an agent that
        leaves a message but never sends BYE), the machine hangs up itself.
        """
        validate_machine(self._spec, allow_dial=False)
        self.start_clock()
        try:
            await asyncio.wait_for(self._run_scripted(), timeout=self._max_duration)
        except asyncio.TimeoutError:
            self.mark("machine_timeout", after_s=round(self._max_duration, 1))
            self.hangup_requested.set()
