"""LiveKit transport for the simulated callee.

Joins the call room as the participant identity the agent expects, runs the
scenario's :class:`SimulatedMachine` over a published audio track, receives the
agent's DTMF presses, and records every remote (agent) audio track to WAV with
a timestamp base shared with the machine timeline — the ground truth the
assertion layer uses for timing checks (message-after-beep, no talk-over).

Why this works without any telephony: the agent's outbound flow gates on
``sip.callStatus`` but explicitly treats a participant WITHOUT that attribute
as active ("clouds/test rigs"), and its AMD/beep-tap/STT all bind to the
participant identity from the dispatch metadata — which is us.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from livekit import api, rtc

from .machine import SAMPLE_RATE, SimulatedMachine

FRAME_MS = 10
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


class TrackAudioOut:
    """AudioOut implementation that pushes PCM into a LiveKit audio source."""

    def __init__(self, source: rtc.AudioSource) -> None:
        self._source = source

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        for start in range(0, samples.shape[0], FRAME_SAMPLES):
            chunk = samples[start : start + FRAME_SAMPLES]
            if chunk.shape[0] < FRAME_SAMPLES:
                chunk = np.pad(chunk, (0, FRAME_SAMPLES - chunk.shape[0]))
            frame = rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=chunk.shape[0],
            )
            await self._source.capture_frame(frame)


@dataclass
class BotResult:
    timeline: list[dict]
    recordings: dict[str, Path]  # track label -> wav path
    joined_at_monotonic: float


class CalleeBot:
    """One simulated callee for one call."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        room_name: str,
        identity: str,
        machine: SimulatedMachine,
        results_dir: Path,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._room_name = room_name
        self._identity = identity
        self._machine = machine
        self._results_dir = results_dir
        self._recorders: dict[str, _TrackRecorder] = {}

    def _token(self) -> str:
        return (
            api.AccessToken(self._api_key, self._api_secret)
            .with_identity(self._identity)
            .with_grants(api.VideoGrants(room_join=True, room=self._room_name))
            .to_jwt()
        )

    async def run(self, *, call_timeout: float = 180.0) -> BotResult:
        room = rtc.Room()
        agent_left = asyncio.Event()

        @room.on("sip_dtmf_received")
        def _on_dtmf(dtmf: rtc.SipDTMF) -> None:
            self._machine.on_dtmf(str(getattr(dtmf, "digit", "") or ""))

        @room.on("track_subscribed")
        def _on_track(track: rtc.Track, pub, participant) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            label = f"{participant.identity}-{pub.sid}"
            recorder = _TrackRecorder(
                track, self._results_dir / f"agent-{label}.wav", self._machine
            )
            self._recorders[label] = recorder
            recorder.start()

        @room.on("participant_disconnected")
        def _on_participant_left(participant) -> None:
            # The agent tearing down (SIP BYE analog: it just leaves/deletes).
            # Only when NO remote participants remain — duplicate job offers
            # can briefly add/remove a second agent participant, and treating
            # that as "agent hung up" made the bot abandon the call mid-script.
            if not room.remote_participants:
                agent_left.set()
            else:
                self._machine.mark(
                    "participant_left_ignored",
                    identity=getattr(participant, "identity", "?"),
                    remaining=len(room.remote_participants),
                )

        @room.on("disconnected")
        def _on_disconnected(*_a) -> None:
            agent_left.set()

        subscribed = asyncio.Event()

        @room.on("local_track_subscribed")
        def _on_local_subscribed(*_a) -> None:
            # The agent subscribed to OUR audio — the greeting can now be heard.
            subscribed.set()

        await room.connect(self._url, self._token())
        joined_at = time.monotonic()
        source = rtc.AudioSource(SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("callee-audio", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        self._machine._audio = TrackAudioOut(source)  # bind transport

        # Don't start "talking" before the agent can hear us. A real callee's
        # greeting starts after the media path is up; starting on join raced
        # the agent's subscription under load and entire prompts went
        # untranscribed (screener scenario flake). Bounded: proceed after 5s
        # even if the event never fires (older SDKs), plus a short settle.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(subscribed.wait(), timeout=5.0)
        await asyncio.sleep(0.5)
        self._machine.mark("audio_path_ready")

        machine_task = asyncio.create_task(self._machine.run())
        try:
            done, _ = await asyncio.wait(
                [machine_task, asyncio.create_task(agent_left.wait())],
                timeout=call_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if machine_task in done and self._machine.hangup_requested.is_set():
                self._machine.mark("bot_leaving", reason="machine_hangup")
            elif machine_task not in done and agent_left.is_set():
                self._machine.mark("agent_hangup_observed")
            else:
                # Give the agent a grace window to finish teardown after the
                # machine script completed (e.g. it is speaking the message).
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(agent_left.wait(), timeout=30.0)
        finally:
            machine_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await machine_task
            for recorder in self._recorders.values():
                await recorder.stop()
            with contextlib.suppress(Exception):
                await room.disconnect()

        timeline = [ev.as_dict() for ev in self._machine.timeline]
        (self._results_dir / "timeline.json").write_text(json.dumps(timeline, indent=2))
        return BotResult(
            timeline=timeline,
            recordings={k: r.path for k, r in self._recorders.items()},
            joined_at_monotonic=joined_at,
        )


class _TrackRecorder:
    """Streams one remote audio track to a WAV, stamping the machine-relative
    start offset into a sidecar json so assertions share one clock."""

    def __init__(self, track: rtc.Track, path: Path, machine: SimulatedMachine) -> None:
        self.path = path
        self._track = track
        self._machine = machine
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        import soundfile as sf

        stream = rtc.AudioStream(self._track)
        started_at = self._machine.now()
        self._machine.mark("agent_track_recording_start", path=self.path.name)
        frames: list[np.ndarray] = []
        sample_rate = SAMPLE_RATE
        try:
            async for event in stream:
                frame = event.frame
                sample_rate = frame.sample_rate
                frames.append(np.frombuffer(frame.data, dtype=np.int16))
        except Exception:
            pass
        finally:
            if frames:
                sf.write(self.path, np.concatenate(frames), sample_rate, subtype="PCM_16")
                self.path.with_suffix(".meta.json").write_text(
                    json.dumps({"started_at": started_at, "sample_rate": sample_rate})
                )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
