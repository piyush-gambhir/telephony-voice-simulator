"""Grade a PSTN sim call from what the number actually captured.

Ground truth in PSTN mode:
  * each ``<Record>`` starts immediately after its segment's compiled audio
    finished playing — for voicemail scenarios that means RIGHT AFTER the
    beep, so the recording's leading silence IS the agent's start delay;
  * ``digits`` are the agent's keypresses as delivered through the real
    carrier DTMF relay;
  * the recordings contain whatever the agent said (the voicemail message it
    left, its post-connect speech).

Checks are derived from the same scenario ``expect`` block the room-level
runner uses, mapped to what is observable on this side of the call:

  message_start_after {mark: tone_end}  → recording leading-silence in
                                          [min, max + RECORD_START_SLACK]
  message_content {expected, min_recall} → transcribe the mailbox recording
                                          (OpenAI) and verify the configured
                                          voicemail message actually landed:
                                          enough of its words present, and
                                          the OPENING words intact (a missing
                                          opening = the agent spoke over the
                                          beep and got truncated)
  dtmf_received {digit}                 → digit seen
  dtmf_press_count_max                  → len(digits) <= max
  agent_spoke                           → speech in any recording
  ended_reason* / detection_layer*      → skipped here (webhook side); the
                                          run is cross-checked in ClickHouse /
                                          the CI dashboard by call id.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ..scenario_validation import validate_expectations
from ..assertions import summarize_checks

# Twilio needs a beat to open the record buffer after <Play> ends.
RECORD_START_SLACK = 0.8


def _norm_tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def _tokens_close(a: str, b: str) -> bool:
    """Whisper-tolerant word equality: exact, or one edit apart.

    Whisper routinely misspells proper nouns (Tony -> Toni);
    that is a transcription artifact, not a truncated message, so content
    grading must not fail on it. Short tokens (< 3 chars) require exact match
    to avoid a/an/at style collisions.
    """
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1 or max(la, lb) < 3:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diff += 1
            if diff > 1:
                return False
            j += 1
    return True


def _opening_start(expected: list[str], got: list[str]) -> int | None:
    """Return where the expected opening starts in the transcript.

    A bag-of-words recall score cannot distinguish a correct voicemail from
    leaked agent speech followed by the correct voicemail. Match the opening
    as an ordered window (while retaining the existing one-word Whisper
    tolerance) so callers can enforce a small, explicit preamble allowance.
    """
    if not expected or len(got) < len(expected):
        return None
    required = max(1, len(expected) - 1)
    for start in range(len(got) - len(expected) + 1):
        window = got[start : start + len(expected)]
        if sum(_tokens_close(a, b) for a, b in zip(expected, window)) >= required:
            return start
    return None


def _allowed_identity_preamble(tokens: list[str], customer_name: str) -> bool:
    """Allow only the normal first-turn identity question before voicemail.

    Outbound agents may begin speaking before AMD finishes. The configured
    first turn ("Hi, is this <name>?") is harmless when the detector then
    identifies voicemail and leaves the complete message. Longer opener or
    conversation leakage remains a failure.
    """
    name = _norm_tokens(customer_name)
    return bool(name) and tokens in (["hi", "is", "this", *name], ["hello", "is", "this", *name])


def _transcribe_elevenlabs(path: Path, key: str) -> str | None:
    import httpx

    model = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
    try:
        with path.open("rb") as fh:
            resp = httpx.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": key},
                data={"model_id": model},
                files={"file": (path.name, fh, "audio/wav")},
                timeout=120,
            )
        resp.raise_for_status()
        return str(resp.json().get("text", "")).strip()
    except Exception:
        return None


def _transcribe_openai(path: Path, key: str) -> str | None:
    import httpx

    try:
        with path.open("rb") as fh:
            resp = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data={"model": os.environ.get("OPENAI_STT_MODEL", "whisper-1"), "language": "en"},
                files={"file": (path.name, fh, "audio/wav")},
                timeout=120,
            )
        resp.raise_for_status()
        return str(resp.json().get("text", "")).strip()
    except Exception:
        return None


def transcribe_recording(path: Path) -> str | None:
    """Transcribe a mailbox recording; None when no backend is configured.

    ElevenLabs is tried first because a deployment that synthesizes the
    scenario corpus already holds that key, so content grading costs no extra
    credential. OpenAI stays as a fallback for setups configured the other way.
    Returning None marks the check indeterminate rather than failed — a
    missing or flaky transcriber is not evidence about the agent.
    """
    if not path.exists():
        return None
    elevenlabs = os.environ.get("ELEVENLABS_API_KEY")
    if elevenlabs:
        text = _transcribe_elevenlabs(path, elevenlabs)
        if text:
            return text
    openai = os.environ.get("OPENAI_API_KEY")
    if openai:
        return _transcribe_openai(path, openai)
    return None


def _speech_segments(path: Path) -> list[tuple[float, float]]:
    from ..assertions import speech_segments_from_wav

    return [(s.start, s.end) for s in speech_segments_from_wav(path)]


def analyze_call(call: dict, raw_scenario: dict, compiled) -> dict[str, Any]:
    expect: dict[str, Any] = raw_scenario.get("expect", {})
    validate_expectations(expect)
    checks: list[dict[str, Any]] = []

    recording_key = (
        "analysis_recording_files" if "analysis_recording_files" in call else "recording_files"
    )
    recordings = [Path(p) for p in call.get(recording_key, [])]
    all_segments: list[tuple[float, float]] = []
    segments_by_path: dict[Path, list[tuple[float, float]]] = {}
    for rec in recordings:
        try:
            segments_by_path[rec] = _speech_segments(rec)
            all_segments.extend(segments_by_path[rec])
        except (OSError, ValueError, RuntimeError) as error:
            checks.append({
                "check": "recording_readable", "passed": False,
                "detail": f"cannot analyze {rec.name}: {error}",
            })

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": passed, "detail": detail})

    if "message_start_after" in expect:
        spec = expect["message_start_after"]
        # Only meaningful when the segment before the first Record ended at
        # the mark (voicemail scenarios put the beep last).
        if not recordings:
            add("message_start_after", False, "no recording captured")
        else:
            first = segments_by_path.get(recordings[0], [])
            if not first:
                add("message_start_after", False, "no speech in recording (message not left?)")
            else:
                delta = first[0][0]
                lo = float(spec.get("min", 0.0))
                hi = float(spec.get("max", 5.0)) + RECORD_START_SLACK
                add(
                    "message_start_after",
                    lo - 0.2 <= delta <= hi,
                    f"leading_silence={delta:.2f}s window=[{lo},{hi:.1f}] (+record slack)",
                )

    if "message_content" in expect:
        spec = expect["message_content"] or {}
        expected = str(spec.get("expected", ""))
        if not recordings or recordings[0] not in segments_by_path:
            add("message_content", False, "no recording captured")
        elif not expected:
            add("message_content", False, "scenario has no expected text")
        else:
            transcript = transcribe_recording(recordings[0])
            if transcript is None:
                # transient whisper failure: retry once before giving up
                transcript = transcribe_recording(recordings[0])
            call["mailbox_transcript"] = transcript
            if transcript is None:
                # API-side transient, not evidence about the message itself.
                # Record as indeterminate (None) so the scenario is not marked
                # failed; the runner's S3-side content check is the backstop.
                checks.append(
                    {
                        "check": "message_content",
                        "passed": None,
                        "detail": "transcription unavailable; message content is unverified",
                    }
                )
            else:
                want = _norm_tokens(expected)
                got = _norm_tokens(transcript)
                got_set = set(got)

                def hit(token: str) -> bool:
                    # Exact, else Whisper-tolerant fuzzy matching (Toni/Tony).
                    return token in got_set or any(_tokens_close(token, g) for g in got)

                recall = sum(1 for t in want if hit(t)) / max(1, len(want))
                head = want[: int(spec.get("head_words", 5))]
                opening_start = _opening_start(head, got)
                head_ok = opening_start is not None
                max_preamble_words = int(spec.get("max_preamble_words", 1))
                preamble = got[:opening_start] if opening_start is not None else []
                identity_ok = _allowed_identity_preamble(
                    preamble, str(spec.get("allowed_identity_name", ""))
                )
                preamble_ok = head_ok and (
                    opening_start <= max_preamble_words
                    or identity_ok
                )
                min_recall = float(spec.get("min_recall", 0.75))
                add(
                    "message_content",
                    recall >= min_recall and preamble_ok,
                    f"recall={recall:.0%} (min {min_recall:.0%}) "
                    f"opening={'intact' if head_ok else 'TRUNCATED'} "
                    f"preamble_words={opening_start if opening_start is not None else 'unknown'} "
                    f"preamble={'allowed identity question' if identity_ok else 'strict'} "
                    f"(otherwise max {max_preamble_words}) "
                    f"transcript={transcript[:120]!r}",
                )

    if "dtmf_received" in expect:
        want = str((expect["dtmf_received"] or {}).get("digit", ""))
        got = [d["digit"] for d in call.get("digits", [])]
        ok = (want in got) if want else bool(got)
        add("dtmf_received", ok, f"want={want or 'any'} got={got}")
    if "dtmf_press_count_max" in expect:
        limit = int(expect["dtmf_press_count_max"])
        count = len(call.get("digits", []))
        add("dtmf_press_count", count <= limit, f"count={count} max={limit}")

    if expect.get("agent_spoke") is not None:
        want = bool(expect["agent_spoke"])
        add(
            "agent_spoke",
            bool(all_segments) == want,
            f"speech_segments={len(all_segments)} recordings={len(recordings)}",
        )

    skipped = [
        k
        for k in expect
        if k.startswith("ended_reason")
        or k.startswith("detection_layer")
        or k == "agent_speech_after"
        or k == "webhook_received"
    ]
    # An unavailable required check is not evidence of a pass. Preserve a
    # known failure; otherwise leave the overall grade indeterminate.
    return {
        "passed": summarize_checks(c["passed"] for c in checks),
        "checks": checks,
        "skipped_webhook_side": skipped,
        "recordings_analyzed": [str(r) for r in recordings],
        "recordings_preserved": [str(r) for r in call.get("recording_files", [])],
    }
