"""Compile scenario YAML into Twilio-executable form.

PSTN mode can't run the step engine live per audio frame — Twilio drives the
call through TwiML request/response. So each scenario sequence is compiled
into SEGMENTS:

  * every run of ``wait`` / ``play`` / ``tone`` steps is pre-rendered into ONE
    wav (silence + assets + synthesized beeps concatenated) — sub-second gaps
    survive, and the beep-end offset inside the file is exact ground truth;
  * a ``hold`` step becomes a ``<Record>`` (this is how we capture what the
    agent says — the voicemail message it leaves, its post-connect speech);
  * a ``hangup`` step becomes ``<Hangup/>``.

``on_dtmf`` wraps the main sequence's first audio in a ``<Gather>`` so the
agent's keypress is captured through the REAL carrier DTMF relay — the one
path the room-level simulator cannot exercise. ``switch`` scenarios continue
into the target sequence; ``ignore`` scenarios log the press and re-prompt,
exactly like a machine that doesn't react.

Compiled audio lands in ``corpus/assets/compiled/<scenario>-<seq>-<i>.wav``
(8 kHz mono s16 — PSTN bandwidth, small files).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..machine import SAMPLE_RATE, synth_tone

PSTN_RATE = 8000
MAX_PROMPT_REPEATS = 3
DEFAULT_GATHER_TIMEOUT = 6


@dataclass
class Segment:
    """One TwiML round-trip: optional audio, then one terminal verb."""

    audio_file: str | None  # compiled wav name (served via /assets)
    audio_duration: float
    terminal: str  # "record" | "hangup" | "pause" | "end" | "dial"
    record_max_s: int = 0
    # For terminal="dial": E.164 target, or the literal "bridge" resolved at
    # runtime from the BRIDGE_NUMBER env (so personal numbers stay out of git).
    dial_target: str = ""
    # Offsets of interesting marks INSIDE audio_file (ground truth for
    # analysis): e.g. tone_end -> seconds from file start.
    marks: dict[str, float] = field(default_factory=dict)


@dataclass
class CompiledScenario:
    name: str
    sequences: dict[str, list[Segment]]
    on_dtmf: Any  # None | "ignore" | {"digits": [...], "switch": seq}
    gather_timeout: int = DEFAULT_GATHER_TIMEOUT

    @property
    def has_gather(self) -> bool:
        return self.on_dtmf is not None


def _resample_to_8k(samples: np.ndarray) -> np.ndarray:
    duration = samples.shape[0] / float(SAMPLE_RATE)
    n = int(duration * PSTN_RATE)
    x_old = np.linspace(0.0, duration, samples.shape[0])
    x_new = np.linspace(0.0, duration, max(1, n))
    return np.interp(x_new, x_old, samples.astype(np.float64)).astype(np.int16)


def _load_asset_48k(assets_dir: Path, name: str) -> np.ndarray:
    import soundfile as sf

    data, rate = sf.read(assets_dir / name, dtype="int16", always_2d=True)
    samples = data[:, 0]
    if rate != SAMPLE_RATE:
        duration = samples.shape[0] / float(rate)
        n = int(duration * SAMPLE_RATE)
        x_old = np.linspace(0.0, duration, samples.shape[0])
        x_new = np.linspace(0.0, duration, max(1, n))
        samples = np.interp(x_new, x_old, samples.astype(np.float64)).astype(np.int16)
    return samples


def compile_scenario(
    scenario: dict[str, Any],
    *,
    assets_dir: Path,
    out_dir: Path,
) -> CompiledScenario:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = scenario["name"]
    machine: dict[str, Any] = scenario["machine"]
    sequences: dict[str, list[Segment]] = {}

    for seq_name, steps in machine.items():
        if seq_name == "on_dtmf":
            continue
        segments: list[Segment] = []
        chunks: list[np.ndarray] = []
        marks: dict[str, float] = {}
        elapsed = 0.0

        def _flush(terminal: str, record_max_s: int = 0, dial_target: str = "") -> None:
            nonlocal chunks, marks, elapsed
            audio_file = None
            duration = 0.0
            if chunks:
                import soundfile as sf

                samples = _resample_to_8k(np.concatenate(chunks))
                audio_file = f"{name}-{seq_name}-{len(segments)}.wav"
                sf.write(out_dir / audio_file, samples, PSTN_RATE, subtype="PCM_16")
                duration = samples.shape[0] / float(PSTN_RATE)
            segments.append(
                Segment(
                    audio_file=audio_file,
                    audio_duration=round(duration, 3),
                    terminal=terminal,
                    record_max_s=record_max_s,
                    dial_target=dial_target,
                    marks=dict(marks),
                )
            )
            chunks, marks, elapsed = [], {}, 0.0

        for step in steps:
            if "wait" in step:
                seconds = float(step["wait"])
                chunks.append(np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16))
                elapsed += seconds
            elif "play" in step:
                samples = _load_asset_48k(assets_dir, str(step["play"]))
                marks[f"play_end:{step['play']}"] = elapsed + samples.shape[0] / SAMPLE_RATE
                chunks.append(samples)
                elapsed += samples.shape[0] / SAMPLE_RATE
            elif "tone" in step:
                spec = step["tone"] or {}
                samples = synth_tone(
                    float(spec.get("freq", 1000.0)),
                    float(spec.get("duration", 0.5)),
                    int(spec.get("amplitude", 12000)),
                )
                elapsed += samples.shape[0] / SAMPLE_RATE
                marks["tone_end"] = elapsed
                chunks.append(samples)
            elif "hold" in step:
                _flush("record", record_max_s=max(1, int(float(step["hold"]))))
            elif "dial" in step:
                # PSTN-only: bridge the caller to a real phone ("bridge" =
                # resolve BRIDGE_NUMBER at runtime — a human tester becomes
                # the person behind the gate/screener).
                _flush("dial", dial_target=str(step["dial"]))
                break
            elif "hangup" in step:
                _flush("hangup")
                break
            elif "repeat_from" in step:
                # PSTN re-prompting is handled by the Gather timeout loop in
                # the server (up to MAX_PROMPT_REPEATS), not by this step.
                continue
            else:
                raise ValueError(f"{name}/{seq_name}: unsupported PSTN step {step!r}")
        if chunks or not segments:
            _flush("end")
        sequences[seq_name] = segments

    on_dtmf = machine.get("on_dtmf")
    if isinstance(on_dtmf, dict) and on_dtmf.get("switch") not in sequences:
        raise ValueError(f"{name}: on_dtmf.switch target missing")
    return CompiledScenario(name=name, sequences=sequences, on_dtmf=on_dtmf)
