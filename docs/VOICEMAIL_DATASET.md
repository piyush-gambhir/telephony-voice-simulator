# Voicemail dataset

A versioned, clean-room dataset of callee-side voicemail, screener, and human
answers for answering machine detection (AMD) testing. It lives apart from the
bundled scenarios: the catalog compiles on demand into a scenario suite and a
text-to-speech corpus, so the repository carries no audio and no extra entries
in `scenarios/` or `corpus/build.py`.

The dataset is code and text only. Prevalence is a coarse bucket (`common`,
`occasional`, `rare`), never a count.

## Taxonomy

The catalog currently has 87 entries in 39 categories.

| Category | Expected class | Expected outcome | Entries |
| --- | --- | --- | --- |
| `carrier_default` | machine-vm | `leave_message_after_beep` | 3 |
| `carrier_number_readback` | machine-vm | `leave_message_after_beep` | 3 |
| `personal_short` | machine-vm | `leave_message_after_beep` | 3 |
| `personal_custom_no_stock_phrase` | machine-vm | `leave_message_after_beep` | 3 |
| `personal_long_rambling` | machine-vm | `leave_message_after_beep` | 2 |
| `personal_starts_like_human` | machine-vm | `leave_message_after_beep` | 3 |
| `multipart_name_spliced` | machine-vm | `leave_message_after_beep` | 2 |
| `business_after_hours` | machine-vm | `leave_message_after_beep` | 2 |
| `business_department_mailbox` | machine-vm | `leave_message_after_beep` | 2 |
| `live_voicemail_transcribing` | machine-vm | `leave_message_after_beep` | 2 |
| `noisy_or_music_greeting` | machine-vm | `leave_message_after_beep` | 2 |
| `spanish_voicemail` | machine-vm | `leave_message_after_beep` | 2 |
| `bilingual_greeting` | machine-vm | `leave_message_after_beep` | 2 |
| `unusual_beep` | machine-vm | `leave_message_after_beep` | 4 |
| `tone_only` | machine-vm | `leave_message_after_beep` | 2 |
| `no_beep_after_greeting` | machine-vm | `leave_message_no_beep` | 2 |
| `call_screener_ai` | screener | `stay_through_screen` | 2 |
| `screener_record_name` | screener | `stay_through_screen` | 2 |
| `dtmf_gate_press_key` | screener | `press_key_then_continue` | 2 |
| `mailbox_full` | machine-unavailable | `hang_up_inbox_full` | 2 |
| `mailbox_not_set_up` | machine-unavailable | `hang_up_unreachable` | 2 |
| `number_disconnected_or_unreachable` | machine-unavailable | `hang_up_unreachable` | 2 |
| `business_ivr_menu` | machine-ivr | `hang_up_ivr` | 2 |
| `human_short_hello` | human | `talk_to_human` | 2 |
| `human_name_answer` | human | `talk_to_human` | 2 |
| `human_busy_driving` | human | `talk_to_human` | 2 |
| `human_screening_by_asking` | human | `talk_to_human` | 2 |
| `human_kid_or_family_answers` | human | `talk_to_human` | 2 |
| `human_business_receptionist` | human | `talk_to_human` | 2 |
| `human_short_confirmation` | human | `talk_to_human` | 4 |
| `human_callback_brushoff` | human | `talk_to_human` | 2 |
| `human_with_background_audio` | human | `talk_to_human` | 2 |
| `hold_or_connecting_then_human` | human | `talk_to_human` | 2 |
| `business_recording_then_human` | human | `talk_to_human` | 2 |
| `ai_screener_recording_notice` | screener | `stay_through_screen` | 2 |
| `greeting_alternative_contact` | machine-vm | `leave_message_after_beep` | 2 |
| `greeting_screens_sales` | machine-vm | `leave_message_after_beep` | 2 |
| `greeting_clipped_start` | machine-vm | `leave_message_after_beep` | 2 |
| `carrier_not_set_up_fast_hangup` | machine-unavailable | `hang_up_unreachable` | 2 |

The last ten categories come from an audit of real outbound calls: shapes that a production
classifier got wrong or that were missing above. Their text is rewritten from scratch (no real
greeting, name, or number is reproduced) and prevalence stays a coarse bucket.

Expected classes and the outcomes each allows:

| Class | Outcomes |
| --- | --- |
| `machine-vm` | `leave_message_after_beep`, `leave_message_no_beep` |
| `machine-unavailable` | `hang_up_inbox_full`, `hang_up_unreachable` |
| `machine-ivr` | `hang_up_ivr` |
| `screener` | `stay_through_screen`, `press_key_then_continue` |
| `human` | `talk_to_human` |

## Schema

`apps/simulator-backend/src/telephony_voice_simulator/datasets/voicemail/catalog.yaml`:

```yaml
version: 1
categories:
  - id: carrier_default
    label: Carrier default greeting
    description: What it is and why it matters for AMD.
    expected_class: machine-vm
    expected_outcome: leave_message_after_beep
entries:
  - id: carrier_default_01
    category: carrier_default
    prevalence: common        # common | occasional | rare
    difficulty: easy          # easy | medium | hard
    language: en              # BCP-47 primary tag
    why_hard: ""              # required when difficulty is medium or hard
    segments:                 # callee side, in order
      - say: {text: "...", voice: female}   # female | male | neutral
      - pause: 1.0
      - tone: {freq: 1000, duration: 0.5}   # amplitude optional, default 12000
      - hold: 45              # callee silent and listening
      - hangup: true          # last segment only
```

`press_key_then_continue` entries also carry `gate_digits` (keys that unlock
the call) and `after_gate` (segments played once a key arrives). Their main
segments must end with a pause, because the prompt repeats until a key press.

To add a category, add it under `categories` with an allowed class and outcome,
then add entries. A new class or outcome needs a matching change to
`OUTCOMES_BY_CLASS` in `catalog.py` and the expectation mapping in `compiler.py`.

## Clean-room policy

Every `say` text, label, description, and `why_hard` passes a lint on load:

- No email addresses or URLs.
- No phone numbers except the fictional 555-0100 through 555-0199 range, in any
  formatting. Spoken-digit forms such as "five five five, zero one two three"
  are allowed.
- No em dashes.

Names and businesses are fictional, and wording is original and vendor neutral:
do not paste real carrier, phone-maker, or customer prompts.

## Compile

```bash
cd apps/simulator-backend
uv run telephony-voice-sim voicemail-dataset stats
uv run telephony-voice-sim voicemail-dataset validate
uv run telephony-voice-sim voicemail-dataset compile --out /tmp/vmds
uv run telephony-voice-sim voicemail-dataset compile --out /tmp/vmds --only mailbox_full_01 human_short_hello_01
```

Compiling writes `scenarios/<n>_<id>.yaml` (scenario name `vmds_<id>`) and
`corpus_text.json` (`{"vmds/<id>_<k>.wav": [voice, text]}`). Each scenario starts
with `wait: 0.8` and is checked with `validate_scenario`. Audio files need not
exist yet.

| Outcome | Expectation |
| --- | --- |
| `leave_message_after_beep` | `ended_reason: call.ending.voicemail-left-message`, `message_start_after` from `tone_end` (0.1 to 3.0 s) |
| `leave_message_no_beep` | same ended reason, `message_start_after` from `play_end` (0.5 to 12.0 s), overlap with the greeting at most 6 s |
| `hang_up_inbox_full` | `ended_reason: call.ending.voicemail-inbox-full`, not `voicemail-left-message` |
| `hang_up_unreachable` | `voicemail-inbox-full`, `voicemail-hangup` or a customer-ended-call reason, not `voicemail-left-message` |
| `hang_up_ivr` | `ended_reason: call.ending.machine-ivr` |
| `stay_through_screen` | `agent_spoke`, speech within 4 s of the last line, not `machine-ivr`, `voicemail-left-message`, or `voicemail-hangup` |
| `press_key_then_continue` | `dtmf_received` for the first gate key, then `agent_spoke` as above, not `machine-ivr` or `voicemail-left-message` |
| `talk_to_human` | same as `stay_through_screen` |

Every scenario also sets `callee_class` (`machine`, `screener`, or `human`) and
`webhook_received: true`. The compiler sets no detection-layer checks and no
`config_overrides`, because those depend on the agent under test.

## Render audio

```bash
uv run telephony-voice-sim voicemail-dataset render --out /tmp/vmds
```

This reads `corpus_text.json` and renders `assets/vmds/<id>_<k>.wav` through the
same TTS backends as `corpus build` (ElevenLabs credentials, or macOS `say`).
It calls paid APIs. Provenance rows go to `assets_manifest.json` in the output
directory with the same shape as the bundled manifest and `commit_allowed: false`.
Keep rendered audio out of the repository (see [the asset policy](ASSET_POLICY.md)).

## Run as a benchmark suite

Point the simulator at the output directory:

```bash
export SIMULATOR_SCENARIOS_DIR=/tmp/vmds/scenarios
export SIMULATOR_CORPUS_ASSETS_DIR=/tmp/vmds/assets
export SIMULATOR_CORPUS_MANIFEST=/tmp/vmds/assets_manifest.json
uv run telephony-voice-sim scenarios validate --all
uv run telephony-voice-sim agents call --scenario vmds_carrier_default_01
```

`SIMULATOR_SCENARIOS_DIR` replaces the bundled AMD scenarios for that process.
See [the benchmark guide](BENCHMARK.md) for suites and repeat runs.
