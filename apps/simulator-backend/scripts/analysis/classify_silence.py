"""Classify outbound silence_timeout callee transcripts into machine/system
buckets to discover scenarios the simulator doesn't cover yet.

Input: a JSONL file of {call_uid, user_text} rows (the callee transcript of
each outbound silence_timeout call), exported from any voice-agent call log.
Usage: python classify_silence.py [calls.jsonl]
"""
import collections
import json
import re
import sys

# JSONL with {"call_uid","user_text"} rows — the callee transcript of each
# outbound silence_timeout call, exported from your agent's call log.
PATH = sys.argv[1] if len(sys.argv) > 1 else "silence_timeout_calls.jsonl"

def norm(t): return re.sub(r"\s+", " ", t.lower().replace("’","'")).strip()

# Ordered: first match wins. Each is (bucket, regex).
RULES = [
    ("carrier_vm_stock", r"you'?ve reached|you have reached|forwarded to|the (person|number|party|google subscriber|wireless customer)|voicemail|voice mail|mailbox|at the (tone|beep)|after the (tone|beep)|record your message|leave (a|your) message|not available to take your call"),
    ("business_vm", r"unable to (get|answer|come to) the phone|we'?re (unable|not available|closed)|our (office|business) (hours|is closed)|leave us a message|we'?ll (call|get) (you )?back|give (us|you) a call ?back|return your call|regular business hours|thank you for calling"),
    ("personal_vm_firstperson", r"i'?m (not|un)available|i can'?t (come to|get to|take) (the|your) (phone|call)|i'?m (away|out|sorry i missed)|can'?t (get|come) to the phone|you know what to do|leave (it|me) a message|sorry i missed your call|i'?ll (call|get) (you )?(right )?back"),
    ("ivr_menu_press", r"press (one|two|three|four|five|six|seven|eight|nine|zero|\d|the|pound|star)|para (espanol|continuar)|for (english|sales|service|support|billing)|main menu|dial (the )?extension|if you know your party"),
    ("screener_saynamer", r"say your name|state your name|screening (your|their|this) call|screening service|who'?s calling|who is (this|calling)|say who|record your name|announce your"),
    ("sit_not_in_service", r"not in service|has been (disconnected|changed)|no longer in service|not a working number|number you (have )?dialed|please check the number|cannot be completed as dialed"),
    ("mailbox_full_unset", r"mailbox is full|not (yet )?set ?up|cannot accept messages|has not (been )?set|is full and"),
    ("spanish", r"\b(hola|gracias|por favor|no est[aá]|mensaje|despu[eé]s|buzon|llamada|espanol|espa[nñ]ol)\b"),
    ("human_greet_then_silent", r"^(hello|hi|hey|yeah|yes|yello|this is|speaking|good (morning|afternoon|evening))\b|who'?s this|can i help you|how can i help"),
    ("repeat_confusion", r"can you (repeat|hear|say)|i (can'?t|couldn'?t|cannot) hear|you'?re breaking up|say (that )?again|are you (there|still there)|hello\?? hello"),
]

rows = []
malformed = 0
with open(PATH) as f:
    for line_number, line in enumerate(f, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            malformed += 1
            print(f"{PATH}:{line_number}: malformed JSON: {exc}", file=sys.stderr)
            continue
        if not isinstance(row, dict):
            malformed += 1
            print(f"{PATH}:{line_number}: expected a JSON object", file=sys.stderr)
            continue
        rows.append(row)

buckets = collections.Counter()
examples = collections.defaultdict(list)
empty = 0
for r in rows:
    ut = norm(r.get("user_text") or "")
    if not ut:
        empty += 1
        buckets["true_silence_no_audio"] += 1
        continue
    for name, pat in RULES:
        if re.search(pat, ut):
            buckets[name] += 1
            if len(examples[name]) < 6:
                examples[name].append(ut[:180])
            break
    else:
        buckets["unclassified"] += 1
        if len(examples["unclassified"]) < 12:
            examples["unclassified"].append(ut[:180])

total = len(rows)
if not total:
    raise SystemExit(f"{PATH}: no valid input rows ({malformed} malformed)")
print(f"TOTAL outbound silence_timeout input rows: {total}")
print(f"MALFORMED rows skipped: {malformed}\n")
print(f"{'bucket':32} {'count':>6} {'share':>7}")
for name, c in buckets.most_common():
    print(f"{name:32} {c:>6} {c/total*100:>6.1f}%")

vm = sum(buckets[b] for b in ("carrier_vm_stock","business_vm","personal_vm_firstperson","mailbox_full_unset"))
sysm = vm + buckets["ivr_menu_press"] + buckets["screener_saynamer"] + buckets["sit_not_in_service"] + buckets["spanish"]
print(f"\n>>> voicemail-ish (undetected VM): {vm} ({vm/total*100:.1f}%)")
print(f">>> any machine/system (VM+IVR+screener+SIT+spanish): {sysm} ({sysm/total*100:.1f}%)")
print(f">>> true silence (no callee audio at all): {buckets['true_silence_no_audio']} ({buckets['true_silence_no_audio']/total*100:.1f}%)")

print("\n=== EXAMPLES ===")
for name, _ in buckets.most_common():
    if name in ("true_silence_no_audio",):
        continue
    print(f"\n--- {name} ---")
    for ex in examples[name][:5]:
        print(f"  • {ex}")
