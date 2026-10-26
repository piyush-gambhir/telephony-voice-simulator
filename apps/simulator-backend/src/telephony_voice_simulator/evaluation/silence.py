"""Classify outbound silence_timeout callee transcripts into machine/system
buckets to discover scenarios the simulator doesn't cover yet.

Input rows are ``{call_uid, user_text}`` objects (the callee transcript of each
outbound silence_timeout call), exported from any voice-agent call log.
"""

from __future__ import annotations

import collections
import re
from typing import Any


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("\u2019", "'")).strip()


# Ordered: first match wins. Each is (bucket, regex).
RULES = [
    (
        "carrier_vm_stock",
        r"you'?ve reached|you have reached|forwarded to|the (person|number|party|google subscriber|wireless customer)|voicemail|voice mail|mailbox|at the (tone|beep)|after the (tone|beep)|record your message|leave (a|your) message|not available to take your call",
    ),
    (
        "business_vm",
        r"unable to (get|answer|come to) the phone|we'?re (unable|not available|closed)|our (office|business) (hours|is closed)|leave us a message|we'?ll (call|get) (you )?back|give (us|you) a call ?back|return your call|regular business hours|thank you for calling",
    ),
    (
        "personal_vm_firstperson",
        r"i'?m (not|un)available|i can'?t (come to|get to|take) (the|your) (phone|call)|i'?m (away|out|sorry i missed)|can'?t (get|come) to the phone|you know what to do|leave (it|me) a message|sorry i missed your call|i'?ll (call|get) (you )?(right )?back",
    ),
    (
        "ivr_menu_press",
        r"press (one|two|three|four|five|six|seven|eight|nine|zero|\d|the|pound|star)|para (espanol|continuar)|for (english|sales|service|support|billing)|main menu|dial (the )?extension|if you know your party",
    ),
    (
        "screener_saynamer",
        r"say your name|state your name|screening (your|their|this) call|screening service|who'?s calling|who is (this|calling)|say who|record your name|announce your",
    ),
    (
        "sit_not_in_service",
        r"not in service|has been (disconnected|changed)|no longer in service|not a working number|number you (have )?dialed|please check the number|cannot be completed as dialed",
    ),
    (
        "mailbox_full_unset",
        r"mailbox is full|not (yet )?set ?up|cannot accept messages|has not (been )?set|is full and",
    ),
    (
        "spanish",
        r"\b(hola|gracias|por favor|no est[aá]|mensaje|despu[eé]s|buzon|llamada|espanol|espa[nñ]ol)\b",
    ),
    (
        "human_greet_then_silent",
        r"^(hello|hi|hey|yeah|yes|yello|this is|speaking|good (morning|afternoon|evening))\b|who'?s this|can i help you|how can i help",
    ),
    (
        "repeat_confusion",
        r"can you (repeat|hear|say)|i (can'?t|couldn'?t|cannot) hear|you'?re breaking up|say (that )?again|are you (there|still there)|hello\?? hello",
    ),
]

VOICEMAIL_BUCKETS = (
    "carrier_vm_stock",
    "business_vm",
    "personal_vm_firstperson",
    "mailbox_full_unset",
)
SYSTEM_BUCKETS = ("ivr_menu_press", "screener_saynamer", "sit_not_in_service", "spanish")
EXAMPLE_LIMITS = {"unclassified": 12}
DEFAULT_EXAMPLE_LIMIT = 6


def _share(count: int, total: int) -> float:
    return round(count / total * 100, 1)


def classify(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Bucket each row's callee transcript and summarize coverage.

    Raises ``ValueError`` when there are no rows.
    """
    total = len(rows)
    if not total:
        raise ValueError("no valid input rows")
    buckets: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for row in rows:
        text = norm(str(row.get("user_text") or ""))
        if not text:
            buckets["true_silence_no_audio"] += 1
            continue
        for name, pattern in RULES:
            if re.search(pattern, text):
                break
        else:
            name = "unclassified"
        buckets[name] += 1
        if len(examples[name]) < EXAMPLE_LIMITS.get(name, DEFAULT_EXAMPLE_LIMIT):
            examples[name].append(text[:180])

    voicemail = sum(buckets[name] for name in VOICEMAIL_BUCKETS)
    system = voicemail + sum(buckets[name] for name in SYSTEM_BUCKETS)
    silence = buckets["true_silence_no_audio"]
    return {
        "total": total,
        "buckets": [
            {"bucket": name, "count": count, "share_percent": _share(count, total)}
            for name, count in buckets.most_common()
        ],
        "voicemail_like": {"count": voicemail, "share_percent": _share(voicemail, total)},
        "any_machine_or_system": {"count": system, "share_percent": _share(system, total)},
        "true_silence": {"count": silence, "share_percent": _share(silence, total)},
        "examples": {name: examples[name] for name, _ in buckets.most_common() if examples[name]},
    }
