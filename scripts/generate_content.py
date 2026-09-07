"""Generate the web console's static scenario reference.

    python scripts/generate_content.py

AMD scenarios come from YAML. IVR and PBX scenarios come from the backend
domain registry so the API, CLI, dry-run engine, and docs share one definition.
"""
from __future__ import annotations

from array import array
import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "apps" / "simulator-backend"
BACKEND_SRC = BACKEND / "src"
PACKAGE = BACKEND_SRC / "telephony_voice_simulator"
OUT = REPO / "apps" / "web-console" / "content" / "scenarios.json"
PUBLIC_AUDIO = REPO / "apps" / "web-console" / "public" / "audio"
PUBLIC_LICENSE = REPO / "apps" / "web-console" / "public" / "LICENSE"
PUBLIC_NOTICE = REPO / "apps" / "web-console" / "public" / "NOTICE"
PUBLIC_NOTICES = REPO / "apps" / "web-console" / "public" / "THIRD_PARTY_NOTICES.md"
_EXPORTED: set[str] = set()
_VISIBLE_AUDIO: frozenset[str] = frozenset()

sys.path.insert(0, str(BACKEND_SRC))
from telephony_voice_simulator.corpus.build_corpus import CORPUS  # noqa: E402
from telephony_voice_simulator.corpus.release_policy import (  # noqa: E402
    exposed_audio_assets,
    local_audio_enabled,
)
from telephony_voice_simulator.domains import scenario_definitions  # noqa: E402
from telephony_voice_simulator.paths import ASSETS_DIR as ASSETS  # noqa: E402
from telephony_voice_simulator.paths import SCENARIOS_DIR as SCENARIOS  # noqa: E402
from telephony_voice_simulator.scenario_validation import validate_scenario  # noqa: E402

CATEGORIES = [
    {
        "id": "voicemail",
        "title": "Voicemail machines",
        "blurb": "The callee's carrier or handset answered with a recorded greeting and (usually) a record beep. The agent must detect the machine, wait for the line to be ready, leave the configured message cleanly after the beep, then hang up. Every mailbox auto-ends after its record window.",
        "names": [
            "stock_voicemail_beep_1000",
            "stock_voicemail_beep_1400_late",
            "multipart_carrier_greeting",
            "custom_greeting_long_no_beep",
            "jet_ski_weird_greeting",
            "verizon_vm_press_options",
            "spanish_voicemail",
            "google_voice_voicemail",
            "number_readback_vm",
            "iphone_live_voicemail_pickup",
            "iphone_live_voicemail_default",
            "ios26_screening_to_voicemail",
            "tone_only_voicemail",
        ],
    },
    {
        "id": "dead-ends",
        "title": "Dead ends",
        "blurb": "No message can be left anywhere: a full mailbox, a mailbox that was never set up, or a disconnected number. The agent must not try to leave a message and the call must end promptly.",
        "names": ["mailbox_full", "att_mailbox_not_setup", "number_disconnected_sit"],
    },
    {
        "id": "gates",
        "title": "Keypress connect-gates",
        "blurb": "An automated gate answers and advances only on a DTMF keypress ('press 5 to be connected'). AMD lumps these into MACHINE_IVR and the old behavior hung up on a reachable human. The agent must send the announced digit, confirm a human connected, and never strand the call.",
        "names": ["dtmf_gate_honored", "dtmf_gate_ignored", "dtmf_gate_any_key", "dtmf_gate_to_human"],
    },
    {
        "id": "screeners",
        "title": "Screeners",
        "blurb": "Interactive AI/voice screeners (Google Pixel Call Screen, iPhone Call Screening, Truecaller Assistant, carrier screening) answer before the human and pass the call through once the caller identifies itself. The agent must state who it is and why it's calling, stay on the line, and greet the human, never hang up, never leave a voicemail.",
        "names": [
            "pixel_call_screen",
            "ios26_screening_to_human",
            "truecaller_assistant",
            "robo_transcription_screener",
            "record_name_screen",
        ],
    },
    {
        "id": "humans",
        "title": "Live humans (precision guards)",
        "blurb": "A real person answered. These exist to prove the agent does not misfire the machine paths on a human, even a gatekeeper who uses voicemail-sounding words, or a slow 'hello'.",
        "names": [
            "gatekeeper_human_take_message",
            "silent_then_human",
            "business_receptionist_human",
        ],
    },
]

REALWORLD = {
    "stock_voicemail_beep_1000": "Standard US carrier voicemail (T-Mobile / AT&T / Verizon).",
    "stock_voicemail_beep_1400_late": "Carriers whose record beep lands seconds late and off 1 kHz.",
    "multipart_carrier_greeting": "Personal greeting + carrier prompt with a silence pocket between the two parts.",
    "custom_greeting_long_no_beep": "Personal answering-machine greetings with no beep and no stock phrasing.",
    "jet_ski_weird_greeting": "Unusual first-person away greetings with no standard carrier phrasing.",
    "verizon_vm_press_options": "Verizon-style mailbox that announces 'press 1 for more options' inside the greeting.",
    "spanish_voicemail": "Spanish-language carrier voicemail; the English phrase list can't match it.",
    "google_voice_voicemail": "Branded virtual-number voicemail behavior observed across common calling platforms.",
    "number_readback_vm": "Carrier voicemail that reads the dialed number back digit-by-digit before the unavailable prompt.",
    "iphone_live_voicemail_pickup": "iOS 17+ Live Voicemail, the human watches a live transcript and picks up mid-message.",
    "iphone_live_voicemail_default": "A short handset voicemail prompt followed by a standard record tone.",
    "ios26_screening_to_voicemail": "An unknown caller is screened first, then forwarded to voicemail when the owner does not answer.",
    "tone_only_voicemail": "A clipped or greeting-disabled answering path where only the record tone reaches the caller.",
    "mailbox_full": "Mailbox full / not accepting messages.",
    "att_mailbox_not_setup": "AT&T-style 'mailbox is not set up yet' dead-end.",
    "number_disconnected_sit": "Disconnected/invalid number with the SIT tri-tone intercept.",
    "dtmf_gate_honored": "Carrier call-screening gate that connects on the right keypress.",
    "dtmf_gate_ignored": "The same gate with a broken keypress path (models a dead relay).",
    "dtmf_gate_any_key": "A 'press any key to continue' gate; exercises the any-key parser branch (resolves to digit 1).",
    "dtmf_gate_to_human": "PSTN-only: pressing the key bridges to a real phone (live manual testing).",
    "pixel_call_screen": "Google Pixel Call Screen behavior: an assistant answers, relays, and the owner may pick up.",
    "ios26_screening_to_human": "iPhone Call Screening interaction where the owner accepts the identified caller.",
    "truecaller_assistant": "Truecaller Assistant, no 'screening' keyword; tests non-keyword detection.",
    "robo_transcription_screener": "AI voice-to-text screener that asks the caller to identify itself and remain connected.",
    "record_name_screen": "Google Voice-style 'say your name after the tone' caller announcement.",
    "gatekeeper_human_take_message": "A live receptionist/family member offering to take a message.",
    "silent_then_human": "Slow human pickup, dead air then 'hello?'.",
    "business_receptionist_human": "A live receptionist answers with a polished business greeting.",
}

CHECK_LABELS = {
    "webhook_received": "call.ended webhook must arrive",
    "ended_reason": "end reason must equal",
    "ended_reason_any_of": "end reason must be one of",
    "ended_reason_not": "end reason must NOT be",
    "detection_layer_prefix_any_of": "detection layer must start with one of",
    "detection_layer_absent": "no voicemail/screener layer (treated as human)",
    "dtmf_received": "agent must send DTMF",
    "dtmf_press_count_max": "max DTMF presses",
    "agent_spoke": "agent must speak",
    "agent_speech_after": "agent must speak after the transition",
    "message_start_after": "message must start after the mark, within window",
    "max_overlap_with_playback": "max talk-over of an asset",
    "message_content": "leave the CONFIGURED message (transcribed & word-matched)",
}


def export_audio(asset: str) -> str | None:
    """Copy a corpus greeting into the web app as a small mp3; return its URL.

    Real machine audio is what makes the docs tangible, the reader hears the
    exact greeting the agent hears. Converted to mp3 (ffmpeg) to keep the
    static bundle light; falls back to copying the wav if ffmpeg is absent.
    """
    if asset not in _VISIBLE_AUDIO:
        return None
    src = ASSETS / asset
    if not src.exists():
        return None
    stem = Path(asset).stem
    if stem in _EXPORTED:
        return f"/audio/{stem}.mp3"
    PUBLIC_AUDIO.mkdir(parents=True, exist_ok=True)
    dst = PUBLIC_AUDIO / f"{stem}.mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-ac", "1", "-b:a", "64k", str(dst)],
            check=True,
        )
        _EXPORTED.add(stem)
        return f"/audio/{stem}.mp3"
    except Exception:
        wav = PUBLIC_AUDIO / f"{stem}.wav"
        wav.write_bytes(src.read_bytes())
        _EXPORTED.add(stem)
        return f"/audio/{stem}.wav"


def step_repr(step: dict) -> dict:
    if "wait" in step:
        return {"kind": "wait", "label": f"wait {step['wait']}s", "detail": "silence"}
    if "hold" in step:
        return {"kind": "hold", "label": f"hold {step['hold']}s", "detail": "record / stay on the line"}
    if "play" in step:
        asset = str(step["play"])
        corpus_entry = CORPUS.get(asset)
        return {
            "kind": "play",
            "label": "play greeting",
            "detail": asset,
            "audioUrl": export_audio(asset),
            "transcript": corpus_entry[1] if corpus_entry else None,
        }
    if "tone" in step:
        t = step["tone"] or {}
        return {"kind": "tone", "label": f"beep {t.get('freq', 1000)} Hz", "detail": f"{t.get('duration', 0.5)}s"}
    if "repeat_from" in step:
        return {"kind": "loop", "label": f"loop to step {step['repeat_from']}", "detail": "re-prompt"}
    if "hangup" in step:
        return {"kind": "hangup", "label": "hang up", "detail": "machine ends the call"}
    if "dial" in step:
        return {"kind": "dial", "label": "bridge to a real phone", "detail": str(step["dial"])}
    return {"kind": "unknown", "label": json.dumps(step), "detail": ""}


def leading_comment(path: Path) -> str:
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            lines.append(line.lstrip("# ").rstrip())
        elif line.strip() == "":
            continue
        else:
            break
    return " ".join(lines)


def machine_repr(machine: dict) -> dict:
    out = {"sequences": [], "onDtmf": None}
    for seq, steps in machine.items():
        if seq == "on_dtmf":
            if steps == "ignore":
                out["onDtmf"] = {"mode": "ignore"}
            else:
                out["onDtmf"] = {"mode": "switch", "digits": [str(d) for d in (steps.get("digits") or [])], "switchTo": steps.get("switch")}
            continue
        if seq == "max_duration":
            out["maxDuration"] = steps
            continue
        out["sequences"].append({"name": seq, "steps": [step_repr(s) for s in steps]})
    return out


def expect_repr(expect: dict) -> list:
    rows = []
    for k, v in expect.items():
        rows.append({"key": k, "label": CHECK_LABELS.get(k, k), "value": v if isinstance(v, (str, int, float, bool)) else json.dumps(v)})
    return rows


def collect_recordings(machine: dict) -> list:
    """Unique greeting assets a scenario plays, in first-seen order, with urls."""
    seen, out = set(), []
    for seq, steps in machine.items():
        if seq in ("on_dtmf", "max_duration"):
            continue
        for st in steps:
            asset = st.get("play") if isinstance(st, dict) else None
            if asset and asset not in seen:
                seen.add(asset)
                url = export_audio(str(asset))
                if url:
                    out.append({"asset": asset, "url": url, "sequence": seq})
    return out


def export_scenario_preview(name: str, machine: dict) -> dict | None:
    """Render the locally simulated callee track, including silence and tones.

    Long ``hold`` windows represent time in which the remote agent speaks or
    the machine records. They are shortened to two seconds in this listening
    preview; prompt timing, inter-prompt waits, and record tones remain exact.
    """
    if not local_audio_enabled():
        return None

    steps = list(machine.get("main") or [])
    on_dtmf = machine.get("on_dtmf")
    if isinstance(on_dtmf, dict):
        steps.extend(machine.get(str(on_dtmf.get("switch"))) or [])

    referenced = {
        str(step["play"])
        for step in steps
        if isinstance(step, dict) and step.get("play")
    }
    if any(asset not in _VISIBLE_AUDIO or not (ASSETS / asset).is_file() for asset in referenced):
        return None

    sample_rate = 48000
    chunks: list[bytes] = []
    duration = 0.0
    compressed = False

    def silence(seconds: float) -> bytes:
        return b"\0\0" * max(0, int(seconds * sample_rate))

    def tone(freq: float, seconds: float, amplitude: int) -> bytes:
        count = max(0, int(seconds * sample_rate))
        fade = min(int(0.01 * sample_rate), count // 2)
        samples = array("h")
        for index in range(count):
            gain = 1.0
            if fade and index < fade:
                gain = index / fade
            elif fade and index >= count - fade:
                gain = (count - 1 - index) / fade
            samples.append(
                int(amplitude * gain * math.sin(2.0 * math.pi * freq * index / sample_rate))
            )
        return samples.tobytes()

    for step in steps:
        if "wait" in step:
            seconds = float(step["wait"])
            chunks.append(silence(seconds))
            duration += seconds
        elif "play" in step:
            with wave.open(str(ASSETS / str(step["play"])), "rb") as source:
                if (
                    source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != sample_rate
                ):
                    return None
                frames = source.readframes(source.getnframes())
                chunks.append(frames)
                duration += source.getnframes() / sample_rate
        elif "tone" in step:
            spec = step["tone"] or {}
            seconds = float(spec.get("duration", 0.5))
            chunks.append(
                tone(
                    float(spec.get("freq", 1000.0)),
                    seconds,
                    int(spec.get("amplitude", 12000)),
                )
            )
            duration += seconds
        elif "hold" in step:
            requested = float(step["hold"])
            seconds = min(requested, 2.0)
            compressed = compressed or requested > seconds
            chunks.append(silence(seconds))
            duration += seconds
        elif "dial" in step:
            chunks.append(silence(1.0))
            duration += 1.0

    if not chunks:
        return None

    wav_path = PUBLIC_AUDIO / f"{name}-scenario-preview.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"".join(chunks))

    mp3_path = PUBLIC_AUDIO / f"{name}-scenario-preview.mp3"
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(wav_path),
                "-ac",
                "1",
                "-b:a",
                "64k",
                str(mp3_path),
            ],
            check=True,
        )
        wav_path.unlink()
        url = f"/audio/{mp3_path.name}"
    except Exception:
        url = f"/audio/{wav_path.name}"

    return {
        "url": url,
        "duration": round(duration, 1),
        "compressed": compressed,
    }



TITLES = {
    "stock_voicemail_beep_1000": "Stock carrier voicemail",
    "stock_voicemail_beep_1400_late": "Late, off-frequency beep",
    "multipart_carrier_greeting": "Two-part carrier greeting",
    "custom_greeting_long_no_beep": "Long personal greeting, no beep",
    "jet_ski_weird_greeting": "Unusual away greeting",
    "verizon_vm_press_options": "Voicemail with 'press 1 for options'",
    "spanish_voicemail": "Spanish-language voicemail",
    "google_voice_voicemail": "Google Voice voicemail",
    "number_readback_vm": "Number-readback voicemail",
    "iphone_live_voicemail_pickup": "iOS Live Voicemail pickup",
    "iphone_live_voicemail_default": "Handset voicemail",
    "ios26_screening_to_voicemail": "iPhone screening to voicemail",
    "tone_only_voicemail": "Tone-only voicemail",
    "mailbox_full": "Mailbox full",
    "att_mailbox_not_setup": "Mailbox not set up",
    "number_disconnected_sit": "Disconnected number",
    "dtmf_gate_honored": "Keypress gate that connects",
    "dtmf_gate_ignored": "Keypress gate that ignores you",
    "dtmf_gate_any_key": "'Press any key' gate",
    "dtmf_gate_to_human": "Keypress gate to a real phone",
    "pixel_call_screen": "Google Pixel Call Screen",
    "ios26_screening_to_human": "iPhone screening to human",
    "truecaller_assistant": "Truecaller Assistant",
    "robo_transcription_screener": "Voice-to-text screener",
    "record_name_screen": "'Say your name' screener",
    "gatekeeper_human_take_message": "Human gatekeeper",
    "silent_then_human": "Slow human pickup",
    "business_receptionist_human": "Business receptionist",
    "pbx_segmented_voicemail_cleanroom": "PBX segmented voicemail (clean-room)",
}

DISPLAY_DESCRIPTIONS = {
    "jet_ski_weird_greeting": (
        "A short personal away greeting with no carrier boilerplate and no record "
        "beep. It proves the agent can recognize voicemail semantically without "
        "misclassifying similar language from a live gatekeeper."
    ),
}

# Why this scenario earns its place, the value, in one or two sentences.
WHY = {
    "stock_voicemail_beep_1000": "The everyday case. If the agent can't leave a clean message here, nothing else matters.",
    "stock_voicemail_beep_1400_late": "A blind fixed delay speaks over a late beep or into dead air. Only real beep alignment lands the message on carriers whose tone drifts seconds late and off 1 kHz.",
    "multipart_carrier_greeting": "The silence pocket between the two greeting parts fools agents into speaking over part two. Proves the agent waits for the real end of the greeting.",
    "custom_greeting_long_no_beep": "No beep and no stock phrase to match, the agent has to recognise a machine from the greeting itself and not talk over 15 seconds of it.",
    "jet_ski_weird_greeting": "Personal greetings carry no keyword the phrase list can catch. This is what the semantic layer exists for.",
    "verizon_vm_press_options": "'Press 1 for more options' inside a real voicemail is a trap: the agent must leave the message and must NOT press anything.",
    "spanish_voicemail": "An English-only phrase list cannot match a Spanish mailbox, so detection must also be acoustic or semantic.",
    "google_voice_voicemail": "A branded virtual-number mailbox pattern distinct enough to exercise as its own regression.",
    "number_readback_vm": "A common undetected-voicemail shape: the carrier reads the dialed digits back before the prompt. Those digits look like an IVR menu but there is none, a trap for keypress logic.",
    "iphone_live_voicemail_pickup": "On iOS Live Voicemail the human is watching a live transcript and can pick up mid-message. The agent has to notice it just became a real conversation.",
    "iphone_live_voicemail_default": "A short handset prompt is easy to talk over while AMD is still deciding. This locks the message to the actual record tone.",
    "ios26_screening_to_voicemail": "Two machine-like systems occur back-to-back. The agent must identify itself to the screener, then wait for the later voicemail tone before leaving the message.",
    "tone_only_voicemail": "Media can connect after a greeting was clipped or a device can expose only its record tone. Beep detection must still place one complete message.",
    "mailbox_full": "Nothing can be left. The agent must recognise the dead end and hang up promptly instead of talking to a wall until the timeout.",
    "att_mailbox_not_setup": "A mailbox that was never set up, same dead end, different words. Guards against leaving a message nowhere.",
    "number_disconnected_sit": "The SIT tri-tone plus 'not in service'. The agent should end the call cleanly, not treat the intercept as a person.",
    "dtmf_gate_honored": "Carrier screening gates are a reachable human behind one keypress. The old behaviour hung up on them; this proves the agent presses the key and gets through.",
    "dtmf_gate_ignored": "When the keypress goes nowhere (a dead relay), the agent must give up gracefully as machine-ivr, never strand the call in a silent timeout.",
    "dtmf_gate_any_key": "'Press any key to continue' is a different parser branch than a specific announced digit; it must resolve to a keypress and connect, not hang up.",
    "dtmf_gate_to_human": "The same gate, but pressing the key rings a real phone so a person can experience exactly what the agent keyed through.",
    "pixel_call_screen": "Google's Assistant answers first and relays. Hang up and you drop a reachable person. The agent must identify itself and wait to be passed through.",
    "ios26_screening_to_human": "The device asks for identity before the owner answers. The agent must identify itself, wait, then greet the person who accepts.",
    "truecaller_assistant": "No 'screening' keyword at all, tests that detection isn't just keyword-matching but recognises the pattern.",
    "robo_transcription_screener": "An AI voice-to-text screener that stays on the line. Easy to hang up on by mistake; it's a screener, not a voicemail.",
    "record_name_screen": "'Say your name after the tone' sounds like a voicemail beep prompt but it's a screener, the tone is a trap.",
    "gatekeeper_human_take_message": "A real person using voicemail-sounding words ('he's not available, can I take a message?'). The agent must NOT mistake them for a machine.",
    "silent_then_human": "Three seconds of dead air then 'hello?'. Guards against the no-speech timeout misfiring on a slow human pickup.",
    "business_receptionist_human": "A polished receptionist greeting can sound prerecorded. This protects the human path from false machine or IVR detection.",
}


OUTCOMES = {
    "att_mailbox_not_setup": (
        "Recognize that the mailbox does not exist, end promptly, and never attempt "
        "to leave a message."
    ),
    "business_receptionist_human": (
        "Wait for the receptionist to finish the greeting, then respond as a live "
        "conversation without activating machine handling."
    ),
    "dtmf_gate_ignored": (
        "Send the announced key no more than twice; if the gate still repeats, end "
        "cleanly as an unreachable IVR instead of waiting forever."
    ),
    "dtmf_gate_to_human": (
        "Send the pound key once and bridge the call to the configured real phone."
    ),
    "iphone_live_voicemail_pickup": (
        "Begin the voicemail message after the tone, detect the person picking up "
        "mid-message, stop voicemail handling, and continue as a live conversation."
    ),
    "ios26_screening_to_voicemail": (
        "Identify the caller to the screener, remain connected through the transition, "
        "then wait for the voicemail tone and leave the configured message."
    ),
    "number_disconnected_sit": (
        "Recognize the intercept tones and disconnected-number announcement, then end "
        "without speaking to it or attempting a voicemail."
    ),
    "silent_then_human": (
        "Wait through the initial silence, respond promptly when the person says hello, "
        "and keep the call on the live-human path."
    ),
}


def derive_outcome(name: str, expect: dict, machine: dict) -> str:
    """One plain-English sentence: what the agent must do."""
    if name in OUTCOMES:
        return OUTCOMES[name]
    er = str(expect.get("ended_reason", ""))
    ern = expect.get("ended_reason_not") or []
    layers = expect.get("detection_layer_prefix_any_of") or []
    if er == "call.ending.voicemail-left-message":
        has_tone = any(
            "tone" in step
            for sequence, steps in machine.items()
            if sequence not in ("on_dtmf", "max_duration")
            for step in steps
        )
        if not has_tone:
            return (
                "Detect the voicemail without relying on a beep, wait for the greeting "
                "to finish, leave the configured message cleanly, then hang up."
            )
        return "Detect the voicemail, wait for the beep, leave the configured message cleanly, then hang up."
    if expect.get("dtmf_received"):
        return "Send the announced key, confirm a human actually connected, then greet them, never hang up on the gate."
    if any("machine-ivr" in x for x in ern) and expect.get("agent_spoke"):
        return "Recognise the screener, say who it is and why it's calling, stay on the line, and greet the human."
    if expect.get("detection_layer_absent"):
        return "Treat the call as a live human and keep the conversation going, no machine handling."
    if er in ("call.ending.voicemail-inbox-full", "call.ending.machine-ivr") or any(
        "inbox-full" in str(x) for x in [er]
    ):
        return "Recognise the dead end and end the call promptly without trying to leave a message."
    if any("voicemail-left-message" in x for x in ern) and expect.get("agent_spoke"):
        return "Keep talking to the person; never drop the canned voicemail message on a live human."
    return "Handle the call correctly for its type without misfiring the machine paths."


def build() -> dict:
    global _VISIBLE_AUDIO

    PUBLIC_AUDIO.mkdir(parents=True, exist_ok=True)
    PUBLIC_NOTICES.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_LICENSE.write_text((REPO / "LICENSE").read_text())
    PUBLIC_NOTICE.write_text((REPO / "NOTICE").read_text())
    PUBLIC_NOTICES.write_text((REPO / "THIRD_PARTY_NOTICES.md").read_text())
    for generated in PUBLIC_AUDIO.iterdir():
        if generated.is_file():
            generated.unlink()
    _EXPORTED.clear()
    _VISIBLE_AUDIO = exposed_audio_assets(set(CORPUS))

    scenarios = {}
    for p in sorted(SCENARIOS.glob("*.yaml")):
        s = validate_scenario(yaml.safe_load(p.read_text()), source=str(p))
        name = s["name"]
        if name in scenarios:
            raise ValueError(f"Duplicate scenario name {name!r} in {p}")
        scenarios[name] = {
            "name": name,
            "kind": "amd",
            "file": p.name,
            "pstnOnly": bool(s.get("pstn_only")),
            "description": DISPLAY_DESCRIPTIONS.get(name, leading_comment(p)),
            "title": TITLES.get(name, name.replace("_", " ").title()),
            "realworld": REALWORLD.get(name, ""),
            "outcome": derive_outcome(name, s.get("expect", {}), s["machine"]),
            "why": WHY.get(name, ""),
            "machine": machine_repr(s["machine"]),
            "expect": expect_repr(s.get("expect", {})),
            "configOverrides": s.get("config_overrides"),
            "preview": export_scenario_preview(name, s["machine"]),
            "recordings": collect_recordings(s["machine"]),
        }
    domain_names: dict[str, list[str]] = {"ivr": [], "pbx": []}
    for definition in scenario_definitions():
        domain_names[definition.kind].append(definition.name)
        scenarios[definition.name] = {
            "name": definition.name,
            "kind": definition.kind,
            "file": "built-in domain model",
            "pstnOnly": definition.pstn_only,
            "description": definition.description,
            "title": definition.title,
            "realworld": (
                "Inbound menu and extension behavior."
                if definition.kind == "ivr"
                else "Business routing, queue, and extension behavior."
            ),
            "outcome": f"Reach the expected terminal outcome: {definition.expected_outcome}.",
            "why": (
                "Validates caller input, retry, dial, and bridge behavior."
                if definition.kind == "ivr"
                else "Validates schedules, direct extensions, queues, fallback, and overflow."
            ),
            "machine": {
                "sequences": [
                    {
                        "name": "main",
                        "steps": [
                            {
                                "kind": step.kind,
                                "label": step.label,
                                "detail": step.detail,
                            }
                            for step in definition.steps
                        ],
                    }
                ],
                "onDtmf": None,
            },
            "expect": [
                {
                    "key": "expected_outcome",
                    "label": "terminal outcome must equal",
                    "value": definition.expected_outcome,
                }
            ],
            "configOverrides": None,
            "preview": None,
            "recordings": [],
        }
    cats = []
    for c in CATEGORIES:
        cats.append({
            "id": c["id"],
            "title": c["title"],
            "blurb": c["blurb"],
            "scenarios": [n for n in c["names"] if n in scenarios],
        })
    cats.extend(
        [
            {
                "id": "ivr",
                "title": "Inbound IVR flows",
                "blurb": "Menu input, retry, extension lookup, dial outcomes, and bridged calls.",
                "scenarios": domain_names["ivr"],
            },
            {
                "id": "pbx",
                "title": "PBX routing",
                "blurb": "Business schedules, departments, queues, direct extensions, fallback, and overflow.",
                "scenarios": domain_names["pbx"],
            },
        ]
    )
    categorized = {name for category in cats for name in category["scenarios"]}
    uncategorized = [name for name in scenarios if name not in categorized]
    if uncategorized:
        cats.append(
            {
                "id": "additional-amd",
                "title": "Additional AMD regressions",
                "blurb": "Newer answering-machine and screening regressions not yet grouped above.",
                "scenarios": uncategorized,
            }
        )
    return {"categories": cats, "scenarios": scenarios, "count": len(scenarios)}


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    content = build()
    OUT.write_text(json.dumps(content, indent=2))
    print(f"wrote {OUT} ({content['count']} scenarios)")
