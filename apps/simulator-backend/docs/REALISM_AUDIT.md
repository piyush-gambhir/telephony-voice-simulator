# AMD scenario-pack realism audit

Last reviewed: 2026-07-27.

## Short answer

The simulator is broad for the single-leg outbound AMD surface, but it is not
"all possible scenarios." No simulator can be 100% exhaustive because carrier
prompts, handset screeners, user voicemail greetings, codec artifacts, region,
language, app versions, and network timing all vary.

It is production-derived and currently covers the important buckets we have
seen: stock carrier voicemail, late/off-frequency beeps, multipart greetings,
custom/no-beep greetings, full/not-set-up/dead numbers, DTMF gates, screeners,
Google Voice, Spanish voicemail, number readback voicemail, iPhone Live
Voicemail pickup, iOS screening-to-human, iOS screening-to-voicemail, and
silent-human pickup. Tone-only voicemail and polished live-receptionist
precision guards cover two additional false-negative/false-positive edges.

## Current scenario coverage

- Stock carrier voicemail with 1 kHz beep.
- Late/off-frequency record beep.
- Multipart voicemail greeting with a silence pocket before the carrier prompt.
- Long custom greeting with no beep.
- Short weird personal greeting with no stock phrase.
- Mailbox full.
- Mailbox not set up.
- Disconnected-number SIT tri-tone.
- Verizon-style voicemail that also mentions key options.
- Spanish voicemail.
- Google Voice / Google Fi-style voicemail.
- Carrier number-readback voicemail.
- Tone-only voicemail when the greeting is clipped or disabled.
- DTMF connect gate: honored, ignored, any-key, and PSTN bridge-to-human.
- Human gatekeeper using voicemail-adjacent words.
- Google Pixel Call Screen-style flow.
- iOS Call Screening-style flow that connects to a human after the agent
  identifies itself.
- iOS Call Screening-style flow that forwards to voicemail after the agent
  identifies itself.
- Truecaller Assistant-style flow.
- Record-name-after-tone screener.
- Robo transcription screener.
- iPhone Live Voicemail pickup mid-message.
- Silent-then-human pickup with first-agent-speech latency assertion.
- Live business receptionist with a polished, automation-like greeting.

The generic screener and one-step iPhone screener fixtures were retired as
duplicates of the more complete branded interaction-shape fixtures. The
remaining screener cases now grade the identity response and the later
human-join response separately. Live Voicemail pickup similarly requires
speech after the person interrupts the mailbox flow.

## Known gaps

- Warm-transfer consult leg reaching voicemail or IVR. The production analysis
  calls this out separately because it needs a two-leg transfer harness.
- Exact carrier audio by region/carrier/account state. The current corpus is
  mostly synthetic TTS text plus synthesized tones.
- Exact Apple/Google/Truecaller voices. Internet audio is usable only when the
  source/license is clear enough for internal testing, or when the team
  explicitly accepts the provenance risk. Prefer our own device/account captures
  because they are reproducible and legally cleaner.
- More languages: Hindi, Hinglish, French, Portuguese, Arabic, and regional
  Spanish variants are not covered yet.
- Early-media and one-way-audio failures: cases where the carrier answers but
  speech never reaches STT are only indirectly represented by silence cases.
- Full PSTN realism in room mode. Room mode skips SIP/carrier behavior; PSTN
  mode is the real validation path.

## Public references used for next targets

- Apple/iOS Live Voicemail lets a recipient view a live transcript and pick up
  while the caller is leaving a message. Source:
  https://www.lifewire.com/iphone-live-voicemail-transcription-7555036
- iOS 26 Call Screening asks unknown callers to state their name/reason before
  the phone rings; the recipient sees a transcription and can answer or ignore.
  Sources:
  https://www.techradar.com/phones/iphone/call-screening-in-ios-26-has-finally-ended-my-spam-call-nightmare-heres-how-to-set-it-up
  https://www.vox.com/technology/462755/apple-iphone-ios26-call-screening
- The closest GitHub project found is not a simulator, but it models four iOS
  call-screening outcomes with transcription + AMD + state transitions. We use
  it as prompt/scenario inspiration only:
  https://github.com/rmc3d/iosCallScreeningTranscriptions
- Special Information Tone (SIT) is a three-tone failure signal; North American
  examples include roughly 985.2 Hz, 1428.5 Hz, and 1776.7 Hz segments. Source:
  https://en.wikipedia.org/wiki/Special_information_tone
- Google/Pixel phone features evolve quickly. Public reports in 2026 mention
  newer Pixel call-assist and voicemail/take-a-message behavior, so Pixel
  scenarios should be periodically re-captured from real devices. Example:
  https://www.techradar.com/phones/google-pixel-phones/google-pixel-voicemail-finally-lets-you-record-custom-greetings-in-a-new-beta

## How to get close to real

Use three tiers, not one:

1. Text/synthetic room mode: fast regression tests for detection logic,
   latency assertions, phrase coverage, and basic timing.
2. PSTN simulator mode: Twilio number answers with the compiled scenario. This
   validates carrier/SIP media, DTMF relay, recording, and webhook behavior.
3. Device capture lab: real iPhone, Pixel, Google Voice, Truecaller, and carrier
   voicemail accounts. Call them from the agent, save the callee-channel audio,
   normalize to 48 kHz mono WAV, then replace/add corpus assets.

The device lab should include:

- iPhone Live Voicemail default greeting.
- iPhone Call Screening / "Ask Reason for Calling."
- Pixel Call Screen.
- Pixel Take a Message / device voicemail if available.
- Google Voice voicemail.
- Truecaller Assistant.
- US carrier voicemail across Verizon, AT&T, T-Mobile, Google Fi, and at least
  one MVNO.
- International/regional voicemail in the languages that show up in prod.

The active source/provenance registry is
`src/telephony_voice_simulator/corpus/assets_manifest.json`. Scenario lint
tests require every
`CORPUS` asset and every scenario `play:` asset to have a manifest row before
it can pass.

To replace a synthetic prompt with a rights-clean internet or device-captured
clip, use the importer:

```bash
uv run python -m telephony_voice_simulator.corpus.import_asset \
  --asset ios26_name_reason_screen.wav \
  --source /tmp/iphone-screening.m4a \
  --source-type internal_device_capture \
  --license-note "Recorded from our test iPhone with consent" \
  --transcript "The person you're calling is screening their calls..." \
  --commit-allowed
```

For internet audio, use `--source-type permissive_internet` or
`official_public`, keep the source URL, and set `--commit-allowed` only when
the license/provenance is acceptable for the repo.

## Acceptance bar for "real enough"

- Every high-volume production AMD bucket has at least one scenario.
- Every known failure mode has a regression assertion, not just `agent_spoke`.
- PSTN matrix passes for core scenarios before a release rollout.
- Device-captured corpus assets cover the top handset/carrier systems.
- Monthly mining of `silence_timeout`, `machine_ivr`, `voicemail-hangup`, and
  `voicemail-left-message` misfires feeds new scenarios back into the corpus.

## Asset provenance rule

Testing-only use does not make an audio asset automatically safe or useful. For
each non-synthetic corpus WAV, keep a manifest row with:

- source URL or capture ID;
- source type: official/public internet, permissive-license library, internal
  device capture, internal carrier capture, or generated;
- license/permission note;
- transcript;
- device/carrier/app/version, when known;
- capture date;
- whether it may be committed to the repo or must stay in private storage.

Best source order:

1. Internal device/carrier captures we are allowed to record.
2. Public-domain / CC0 / permissive-license audio with a saved license link.
3. Publicly documented text re-rendered through synthetic voices.
4. Random public videos/audio only with explicit team approval and a provenance
   note, because exact assistant/carrier voices are often proprietary and can
   disappear or change.
