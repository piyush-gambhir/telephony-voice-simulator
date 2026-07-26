# Audio asset policy

Corpus audio is local/internal material by default. Git, wheels, source
archives, static web builds, and Docker build contexts exclude WAV and MP3
files unless the repository owner makes an explicit per-file publication
decision.

## Provenance

`apps/simulator-backend/src/telephony_voice_simulator/corpus/assets_manifest.json`
contains exactly one row for every corpus entry. Scenario lint tests enforce
that relationship.

Each row records:

- Source type and source location
- Licensing or consent note
- Transcript source and voice hint
- Whether committing the exact file is explicitly allowed

`commit_allowed: false` means the binary must not enter the public repository,
even if it is useful locally. All current rows are false because exact
redistribution permission remains unresolved.

## Public-release allowlist

`corpus/public_release_allowlist.json` is the second, independent gate. It is
initially empty. A manifest row alone never publishes a binary.

Before adding one asset to a public release:

1. Confirm the prompt text, recording consent, TTS terms, voice rights, and
   redistribution terms.
2. Record exact engine/model/voice/date and SHA-256 in the manifest, then set
   only that row's `commit_allowed` to true.
3. Add the exact filename to `public_release_allowlist.json`.
4. Add exact-file exceptions to Git/Docker ignores and, if the wheel should
   contain it, an exact package-data entry. Never add an audio wildcard.
5. Run `pnpm public-release:check`, build the wheel and web console, and check
   those artifacts again.

`scripts/check_public_release.py` rejects tracked or non-ignored untracked,
web-exported, or archived
audio that is not allowlisted and checksum-bound.

Existing files under `corpus/assets/` remain usable during local development.
Set `SIMULATOR_INCLUDE_LOCAL_AUDIO=true` only when a local web build should
export them for playback. The content build also renders one clean-room
callee-side preview per AMD scenario, combining the exact prompt order,
inter-prompt silence, and synthesized tones. Long agent-response and mailbox
recording windows are shortened to two seconds in previews and are labeled as
such in the console:

```bash
SIMULATOR_INCLUDE_LOCAL_AUDIO=true pnpm build
```

Never set that variable in a public build.

## Public PSTN deployment allowlist

A private corpus mounted into a container is private only at rest. AMD mode
compiles its source files and serves the generated WAVs from intentionally
unauthenticated `/assets/` URLs so Twilio can fetch them. Anyone who obtains or
guesses one of those URLs can fetch that generated audio.

Public-serving approval is distinct from repository redistribution approval.
Set `PSTN_AUDIO_DEPLOYMENT_ALLOWLIST` to a private version-1 JSON file with the
same strict shape:

```json
{
  "version": 1,
  "assets": ["rights-reviewed-greeting.wav"]
}
```

An explicit deployment allowlist requires matching source/license provenance
and an exact SHA-256 in the private manifest; it does not require
`commit_allowed: true`. That flag remains exclusively the gate for putting a
binary in public Git or release artifacts.

The deployment allowlist must cover every `play` asset referenced by the
loaded AMD scenario catalog, not only the default scenario. Validate it before
exposing the service:

```bash
cd apps/simulator-backend
uv run python scripts/validate_public_audio.py \
  --allowlist /absolute/path/to/public_audio_allowlist.json \
  --assets-dir /absolute/path/to/private-corpus/assets \
  --manifest /absolute/path/to/private-corpus/assets_manifest.json
```

If an asset may be used internally but may not be served to unauthenticated
users, do not approve it and do not start public AMD mode. Use `--with-ivr` or
`compose.ivr.yaml` when no public AMD audio is needed. Never put customer
calls, real voicemail captures, personal data, secrets, or audio without
public-serving rights into a public PSTN corpus.

## Clean-room PBX fixture

`pbx_segmented_voicemail_cleanroom` replaces the omitted vendor-audio PBX
regression. Its prompt text was authored for this simulator, each phrase is
independently generated, and the tone is synthesized by the state machine. It
does not copy a vendor transcript, sound file, or voice.

The current local WAV rows record their generator, voice, date, and checksum.
That proves which binary was reviewed; it does not grant redistribution
rights. Confirm the applicable TTS terms before allowlisting the files, or
regenerate them with an approved engine and update the manifest.

Regenerate local source assets with:

```bash
cd apps/simulator-backend
uv run python -m telephony_voice_simulator.corpus.build_corpus --only pbx_
```

Do not rename corpus files casually: scenario YAML and compiled PSTN programs
use stable asset names.

## Recording imports

`corpus/from_recordings.py` extracts into a temporary directory and delegates
to `corpus/import_asset.py`. The import gate validates stable asset names,
normalizes audio, writes provenance, and defaults `commit_allowed` to false.
S3 access or recording ownership never implies public redistribution rights.

## Optional IVR prompts

The standalone Twilio Functions app uses `<Say>` without generated files.
`scripts/generate_ivr_prompts.py` can render the clean-room prompt source
through a configured provider and writes a checksum/provenance manifest.

Generated MP3s are ignored because the selected provider, account, and voice
determine redistribution rights. Review the emitted manifest and applicable
terms before deploying or distributing them.
