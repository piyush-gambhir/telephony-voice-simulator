# Voicemail dataset

A versioned, clean-room dataset of callee-side voicemail, screener, and human
answers for answering machine detection (AMD) testing. It lives apart from the
bundled scenarios: the catalog compiles on demand into a scenario suite and a
text-to-speech corpus, so the repository carries no audio and no extra entries
in `scenarios/` or `corpus/build.py`. The same catalog exports labelled text
cases for offline AMD evaluation with no calls at all.

The dataset is code and text only. Prevalence is a coarse bucket (`common`,
`occasional`, `rare`), never a count.

## Taxonomy

The catalog (version 2) has 127 entries in 50 categories.

| Category | Expected class | Expected outcome | Entries |
| --- | --- | --- | --- |
| `carrier_default` | machine-vm | `leave_message_after_beep` | 3 |
| `carrier_number_readback` | machine-vm | `leave_message_after_beep` | 3 |
| `personal_short` | machine-vm | `leave_message_after_beep` | 4 |
| `personal_custom_no_stock_phrase` | machine-vm | `leave_message_after_beep` | 3 |
| `personal_long_rambling` | machine-vm | `leave_message_after_beep` | 2 |
| `personal_starts_like_human` | machine-vm | `leave_message_after_beep` | 3 |
| `multipart_name_spliced` | machine-vm | `leave_message_after_beep` | 2 |
| `business_after_hours` | machine-vm | `leave_message_after_beep` | 2 |
| `business_department_mailbox` | machine-vm | `leave_message_after_beep` | 2 |
| `live_voicemail_transcribing` | machine-vm | `leave_message_after_beep` | 2 |
| `noisy_or_music_greeting` | machine-vm | `leave_message_after_beep` | 2 |
| `spanish_voicemail` | machine-vm | `leave_message_after_beep` | 3 |
| `bilingual_greeting` | machine-vm | `leave_message_after_beep` | 2 |
| `unusual_beep` | machine-vm | `leave_message_after_beep` | 5 |
| `tone_only` | machine-vm | `leave_message_after_beep` | 3 |
| `no_beep_after_greeting` | machine-vm | `leave_message_no_beep` | 3 |
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
| `low_tone_before_beep` | machine-vm | `leave_message_after_beep` | 3 |
| `carrier_talks_after_tone_cue` | machine-vm | `leave_message_after_beep` | 3 |
| `carrier_readback_long_pause` | machine-vm | `leave_message_after_beep` | 3 |
| `late_greeting_after_silence` | machine-vm | `leave_message_after_beep` | 2 |
| `screener_then_voicemail` | screener | `leave_message_after_screen` | 3 |
| `greeting_asks_name_number_message` | machine-vm | `leave_message_after_beep` | 3 |
| `greeting_detailed_or_brief_message` | machine-vm | `leave_message_after_beep` | 4 |
| `human_says_voicemail_phrases` | human | `talk_to_human` | 4 |
| `stray_yes_before_carrier` | machine-vm | `leave_message_after_beep` | 3 |
| `mailbox_full_after_greeting` | machine-unavailable | `hang_up_inbox_full` | 3 |
| `unreachable_announcement_no_sit` | machine-unavailable | `hang_up_unreachable` | 4 |

The ten categories from `human_short_confirmation` to `carrier_not_set_up_fast_hangup`
come from an audit of real outbound calls: shapes that a production classifier got wrong
or that were missing above. The eleven categories from `low_tone_before_beep` onwards
(catalog version 2) fill the gaps that audit listed. Their text is rewritten from scratch
(no real greeting, name, or number is reproduced) and prevalence stays a coarse bucket.

Record beeps sit mostly at 450, 650 and 750 Hz, the common mailbox tones. A 1000 Hz
beep is rare and appears in two entries (`unusual_beep_03`, `tone_only_01`); a few
others use 900, 1400 or 2100 Hz.

Expected classes and the outcomes each allows:

| Class | Outcomes |
| --- | --- |
| `machine-vm` | `leave_message_after_beep`, `leave_message_no_beep` |
| `machine-unavailable` | `hang_up_inbox_full`, `hang_up_unreachable` |
| `machine-ivr` | `hang_up_ivr` |
| `screener` | `stay_through_screen`, `press_key_then_continue`, `leave_message_after_screen` |
| `human` | `talk_to_human` |

## Schema

`apps/simulator-backend/src/telephony_voice_simulator/datasets/voicemail/catalog.yaml`:

```yaml
version: 2
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
    cues: ["at the tone"]     # optional; each phrase must occur in the entry's say text
    failure_modes: [early_message]          # nonempty, from the fixed list below
    provenance: {basis: invented, added_in: 1}
    segments:                 # callee side, in order
      - say: {text: "...", voice: female}   # female | male | neutral
      - pause: 1.0
      - tone: {freq: 650, duration: 0.5, role: beep}   # amplitude optional, default 12000
      - hold: 45              # callee silent and listening
      - hangup: true          # last segment only
```

Loading is strict: unknown keys, an unknown version, or any value outside these lists
fails validation.

- `tone.role` is required: `beep` (the record cue), `noise` (a sound that is not a cue,
  such as a hum, click or short blip before the beep), `music` (hold or background
  music), `sit` (special information tones), or `screen` (a screener's name prompt).
  `leave_message_after_beep` and `leave_message_after_screen` need at least one
  `beep` tone.
- `cues` are the trigger phrases a phrase-based classifier would react to, compared
  case-insensitively against the joined `say` text of the entry (including
  `after_gate`). Human entries list the machine-sounding phrases they contain.
- `failure_modes` name how an agent is expected to fail on the entry:
  `false_human`, `false_machine`, `early_message`, `misses_beep`,
  `waits_for_missing_beep`, `talks_over_greeting`, `late_greeting`,
  `gives_up_on_silence`, `hangs_up_early`, `stt_overlap`, `message_on_unavailable`,
  `ignores_screener`, `misses_key`.
- `provenance.basis` is `audit` (a shape reported by an audit of real calls),
  `public_standard` (a published signal such as SIT tones) or `invented`.
  `provenance.added_in` is the catalog version that added the entry.

`press_key_then_continue` entries also carry `gate_digits` (keys that unlock
the call) and `after_gate` (segments played once a key arrives). Their main
segments must end with a pause, because the prompt repeats until a key press.

To add a category, add it under `categories` with an allowed class and outcome,
then add entries. A new class or outcome needs a matching change to
`OUTCOMES_BY_CLASS` in `catalog.py` and the expectation mapping in `compiler.py`.
A schema change bumps `version` and `SUPPORTED_VERSION` together.

## Clean-room policy

Every `say` text, cue, label, description, and `why_hard` passes a lint on load:

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

Compiling writes, under the output directory:

- `scenarios/<n>_<id>.yaml`, scenario name `vmds_<id>`. Each starts with
  `wait: 0.8`, passes `validate_scenario`, and carries a `metadata:` block with
  the entry's labels. Tone roles are dropped from the compiled steps.
- `corpus_text.json`: `{"vmds_<id>_<k>.wav": [voice, text]}`. Asset names are flat
  so the public-audio allowlist can list them.
- `labels.jsonl`: one row per compiled entry, the same object as the scenario
  `metadata:` block.
- `public_audio_allowlist.candidate.json`: every asset name, in the allowlist
  format. It is a review list, not an approval (see below).

Audio files need not exist yet. A label row holds the authored fields (`id`,
`category`, `expected_class`, `outcome`, `language`, `difficulty`, `prevalence`,
`failure_modes`, `cues`, `provenance`) and computed ones:

| Field | Meaning |
| --- | --- |
| `callee_class` | 3-way benchmark class: `machine`, `screener` or `human` |
| `gold_amd` | `human`, `machine-vm`, `machine-unavailable` or `machine-ivr`; screeners, DTMF gates and IVR menus are `machine-ivr` |
| `split` | `dev` or `test`, about 70/30, from a SHA-256 of the id, so it never changes for an entry |
| `opening_text` | speech before the first `beep` tone, hold, hangup or DTMF gate |
| `full_text`, `opening_words`, `full_words` | all speech, including `after_gate`, and word counts |
| `timing` | from the main segments, without audio: `leading_silence_s` (initial wait plus leading pauses), `pauses_s`, `longest_pause_s`, `beep_count`, `beep_freqs_hz`, `beep_durations_s` |

A screener that hands off to voicemail (`leave_message_after_screen`) has
`callee_class: machine`, because the benchmark derives the observed class from how
the call ended and this call must end with a left message.

| Outcome | Expectation |
| --- | --- |
| `leave_message_after_beep` | `ended_reason: call.ending.voicemail-left-message`, `message_start_after` from `tone_end` (0.1 to 3.0 s) |
| `leave_message_after_screen` | same as `leave_message_after_beep`; `tone_end` is the final beep |
| `leave_message_no_beep` | same ended reason, `message_start_after` from `play_end:<last greeting asset>` (0.5 to 12.0 s), overlap with the greeting at most 6 s |
| `hang_up_inbox_full` | `ended_reason: call.ending.voicemail-inbox-full`, not `voicemail-left-message` |
| `hang_up_unreachable` | `voicemail-inbox-full`, `voicemail-hangup` or a customer-ended-call reason, not `voicemail-left-message` |
| `hang_up_ivr` | `ended_reason: call.ending.machine-ivr` |
| `stay_through_screen` | `agent_spoke`, speech within 4 s of the last line, not `machine-ivr`, `voicemail-left-message`, or `voicemail-hangup` |
| `press_key_then_continue` | `dtmf_received` for the first gate key, then `agent_spoke` as above, not `machine-ivr` or `voicemail-left-message` |
| `talk_to_human` | same as `stay_through_screen` |

Every scenario also sets `callee_class` and `webhook_received: true`. The compiler
sets no detection-layer checks and no `config_overrides`, because those depend on
the agent under test.

On the phone path, `message_start_after` and `agent_speech_after` are timed from the
first `<Record>` window: the delay is the compiled audio after the mark plus the
first speech in the recording. When that speech starts within 0.2 s of the recording
and the delay is inside the window, the check is indeterminate: the agent may have
been talking over the greeting, or the recording may have started late.

## Render audio

```bash
uv run telephony-voice-sim voicemail-dataset render --out /tmp/vmds
```

This reads `corpus_text.json` and renders `assets/vmds_<id>_<k>.wav` through the
same TTS backends as `corpus build` (ElevenLabs credentials, or macOS `say`).
It calls paid APIs. Provenance rows go to `assets_manifest.json` in the output
directory with the same shape as the bundled manifest and `commit_allowed: false`.
Keep rendered audio out of the repository (see [the asset policy](ASSET_POLICY.md)).

## Run as a benchmark suite

Point the simulator at the output directory. The server and the benchmark CLI each
read these variables, so set them for both processes:

```bash
export SIMULATOR_SCENARIOS_DIR=/tmp/vmds/scenarios
export SIMULATOR_CORPUS_ASSETS_DIR=/tmp/vmds/assets
export SIMULATOR_CORPUS_MANIFEST=/tmp/vmds/assets_manifest.json
uv run telephony-voice-sim scenarios validate --all
uv run telephony-voice-sim agents call --scenario vmds_carrier_default_01
```

`SIMULATOR_SCENARIOS_DIR` replaces the bundled AMD scenarios for that process.
See [the benchmark guide](BENCHMARK.md) for suites and repeat runs. The
`scripts/run_benchmark.sh` preflight uses a bundled scenario by default; set
`BENCHMARK_PREFLIGHT_SCENARIO=vmds_carrier_default_01` when the dataset replaces
the bundled catalog.

### Serve the audio over PSTN

Public AMD mode serves compiled audio from unauthenticated `/assets` URLs, so it
refuses to start until every referenced asset is approved (see
[the asset policy](ASSET_POLICY.md)). Approval is the repository owner's decision;
the compiler never makes it.

1. Compile and render as above.
2. Listen to the rendered files and check the TTS provider's output terms. Copy
   `public_audio_allowlist.candidate.json` to a private reviewed file, for example
   `/tmp/vmds/public_audio_allowlist.json`, removing anything you do not approve.
3. Validate the reviewed file against the rendered corpus:

   ```bash
   uv run telephony-voice-sim corpus validate-public \
     --allowlist /tmp/vmds/public_audio_allowlist.json \
     --assets-dir /tmp/vmds/assets \
     --manifest /tmp/vmds/assets_manifest.json \
     --scenarios-dir /tmp/vmds/scenarios
   ```

4. Start the server with the three `SIMULATOR_*` variables above plus
   `PSTN_AUDIO_DEPLOYMENT_ALLOWLIST=/tmp/vmds/public_audio_allowlist.json` and
   `PSTN_ACKNOWLEDGE_PUBLIC_AUDIO=true`. For a Twilio-hosted deploy, pass the same
   file to `--audio-deployment-allowlist` with `--acknowledge-public-audio`.

## Offline evaluation

Export labelled text cases, produce predictions with any classifier, and score them.
Nothing here places a call or reads audio.

```bash
uv run telephony-voice-sim voicemail-dataset export --out /tmp/vmds/cases.jsonl
uv run telephony-voice-sim voicemail-dataset export --variants --out /tmp/vmds/cases_stt.jsonl
uv run telephony-voice-sim voicemail-dataset export --format benchmark --out /tmp/vmds/cases.json
uv run telephony-voice-sim voicemail-dataset baseline --out /tmp/vmds/preds.jsonl
uv run telephony-voice-sim voicemail-dataset score \
  --cases /tmp/vmds/cases.jsonl --predictions /tmp/vmds/preds.jsonl
```

- `export` writes one JSONL row per entry with `id`, `category`, `expected_class`,
  `callee_class`, `gold_amd`, `outcome`, `language`, `difficulty`, `prevalence`,
  `split`, `failure_modes`, `cues`, `opening_text`, `full_text` and
  `variant: original`.
- `--variants` adds STT-style rows with ids `<id>__stt_plain` (lowercase, punctuation
  removed except apostrophes), `<id>__stt_digits` (also digits spoken one by one) and
  `<id>__stt_clipped` (the first three words dropped, as if lost under the agent's
  opener). A variant identical to an earlier row for the same entry is skipped.
- `--format benchmark` writes a JSON list of
  `{"id", "source": "vmds", "gold", "transcript"}` with `gold` set to `gold_amd`
  and `transcript` set to `opening_text`.
- `score` reads prediction rows `{"id", "predicted", "decided_at_s"?}`. `predicted`
  may use the AMD labels or the dataset classes (`screener` maps to `machine-ivr`);
  `uncertain` and `postpone` count as abstentions. Accuracy is correct predictions
  over scored cases, so an abstention counts as wrong; cases with no prediction are
  listed as `missing_ids` and not scored. The report has a `gold_amd` by predicted
  confusion matrix, accuracy by category, difficulty, language and split, the
  median `decided_at_s`, and these counts with ids: `humans_called_machine`,
  `screeners_called_machine` (callee class `screener` predicted as `machine-vm` or
  `machine-unavailable`), `machines_called_human`, `abstentions`, `missing_ids`
  and `extra_ids`. Add `--json` for the full report as JSON.
- `baseline` runs the callee-transcript regex rules from `evaluation/silence.py`
  over `opening_text`. Mailbox buckets map to `machine-vm`, full or not-in-service
  buckets to `machine-unavailable`, menu and screener buckets to `machine-ivr`, and
  greeting or confusion buckets to `human`. Spanish, unclassified and empty text
  become `uncertain`.

Tune phrase lists or thresholds on the `dev` split and report on `test`.

## Future work

- Callee behaviours the runtime cannot express yet: a recorder that stops after a
  few seconds of silence, and a callee that waits for the agent to speak first.
  Short human answers such as "Yes." play at a fixed time today.
- Audio-derived labels such as greeting end and beep start. They need rendered
  audio durations and per-tone start marks from the Twilio compiler, which today
  keeps only the last `tone_end` of each segment.
- Timing grading from the dual-channel call recording, so speech during the
  greeting, overlap with playback and answers to a screener become visible on the
  phone path.
- Per-segment language tags for bilingual entries.
