"""Exercise LiveKit buffering semantics without a LiveKit server."""

import asyncio

import numpy as np
import pytest

from telephony_voice_simulator.bot import TrackAudioOut
from telephony_voice_simulator.machine import SAMPLE_RATE


class BufferedSource:
    def __init__(self):
        self.queued = asyncio.Event()
        self.drained = asyncio.Event()
        self.cleared = False

    async def capture_frame(self, frame):
        self.queued.set()

    async def wait_for_playout(self):
        await self.drained.wait()

    def clear_queue(self):
        self.cleared = True


async def test_play_waits_for_audible_completion():
    source = BufferedSource()
    task = asyncio.create_task(TrackAudioOut(source).play(np.zeros(480, dtype=np.int16), SAMPLE_RATE))
    await source.queued.wait()
    assert not task.done()
    source.drained.set()
    await asyncio.wait_for(task, timeout=0.5)
    assert not source.cleared


async def test_cancel_discards_buffered_greeting():
    source = BufferedSource()
    task = asyncio.create_task(TrackAudioOut(source).play(np.zeros(480, dtype=np.int16), SAMPLE_RATE))
    await source.queued.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.cleared
