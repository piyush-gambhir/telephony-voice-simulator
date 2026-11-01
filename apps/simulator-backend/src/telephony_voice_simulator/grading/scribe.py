"""Independent transcripts of persona calls from their two-channel recordings (ElevenLabs Scribe).

A persona's own transcript is what its live speech recognition heard, in whole seconds. A
dual-channel Twilio recording of the same call, transcribed by Scribe with word timestamps,
gives a second opinion on the words and timings to a tenth of a second. Twilio records the
far party (the agent under test, then a bridged customer or employee) on channel 0 and this
simulator's side on channel 1, and both start when the call is answered.
"""

from __future__ import annotations

import os
import statistics
from pathlib import Path
from typing import Any

# Words further apart than this start a new utterance.
UTTERANCE_GAP_S = 0.8
REMOTE, LOCAL = 0, 1


def transcribe(path: Path, key: str | None = None) -> list[dict[str, Any]] | None:
    """Word lists per channel, or None when Scribe is unavailable or fails."""
    import httpx

    key = key or os.environ.get("ELEVENLABS_API_KEY", "")
    if not key or not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            resp = httpx.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": key},
                data={"model_id": os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2"),
                      "use_multi_channel": "true", "timestamps_granularity": "word"},
                files={"file": (path.name, fh, "audio/wav")},
                timeout=180,
            )
        resp.raise_for_status()
        body = resp.json()
    except Exception:
        return None
    channels = body.get("transcripts") or [body]
    return [
        {"channel": int(t.get("channel_index") or 0),
         "words": [{"text": w["text"], "start": float(w["start"]), "end": float(w["end"])}
                   for w in t.get("words") or [] if w.get("type") == "word"]}
        for t in channels
    ]


def utterances(words: list[dict[str, Any]], channel: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for word in words:
        if out and word["start"] - out[-1]["end"] <= UTTERANCE_GAP_S:
            out[-1]["text"] += " " + word["text"]
            out[-1]["end"] = word["end"]
        else:
            out.append({"channel": channel, "text": word["text"],
                        "start": word["start"], "end": word["end"]})
    return out


def summarize(channels: list[dict[str, Any]], offset: float = 0.0) -> dict[str, Any]:
    """Remote and local text, time to the far party's first word, and response gaps.

    ``offset`` is how long after answering the recording began; it moves the first word
    onto the call's clock (gaps between words do not depend on it).

    A response gap runs from the end of something this side said to the far party's next
    utterance that starts after it: what the caller experiences as the agent's latency.
    """
    by = {c["channel"]: c["words"] for c in channels}
    remote = utterances(by.get(REMOTE, []), REMOTE)
    local = utterances(by.get(LOCAL, []), LOCAL)
    gaps = []
    for said in local:
        reply = next((u for u in remote if u["start"] >= said["end"]), None)
        # Only a reply that came before this side spoke again answers this utterance.
        if reply and not any(said["end"] < u["start"] < reply["start"] for u in local):
            gaps.append(round(reply["start"] - said["end"], 2))
    return {
        "remote_text": " ".join(u["text"] for u in remote),
        "local_text": " ".join(u["text"] for u in local),
        "turns": sorted(remote + local, key=lambda u: u["start"]),
        "ttfw_s": round(remote[0]["start"] + offset, 2) if remote else None,
        # Time to first word measures the agent only when it spoke first.
        "caller_spoke_first": bool(local and (not remote or local[0]["start"] < remote[0]["start"])),
        "recording_offset_s": offset,
        "response_gap_p50_s": round(statistics.median(gaps), 2) if gaps else None,
        "response_gap_p95_s": _p95(gaps),
        "response_gaps": len(gaps),
    }


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 2)
