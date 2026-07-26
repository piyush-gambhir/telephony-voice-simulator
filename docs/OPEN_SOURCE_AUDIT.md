# Open-source and proprietary-material audit

Last reviewed: 2026-07-26.

This is an engineering release-readiness assessment, not legal advice.

## Recommendation

Open-source the platform code. There is no secret algorithm or technically
exclusive mechanism here that another capable team could not reproduce. The
durable value is the maintained scenario catalog, production feedback loop,
real-device/carrier lab, grading discipline, and operational integrations.
Open sourcing can improve adapter coverage and scenario quality without
giving away an irreplaceable technical primitive.

Treat the audio corpus as a separate release gate.

## Retained private data and moat

The legacy `PROD_AMD_ANALYSIS.md` and `SILENCE_TIMEOUT_ANALYSIS.md` reports were
intentionally not migrated. They contain production-derived distributions,
counts, rates, miss clusters, and failure-mining conclusions rather than
runtime code. The generalized classifier and public realism guidance remain,
so simulator functionality is complete without publishing those operational
measurements.

Those reports, private recordings, the continuously refreshed miss corpus, and
the real-device/carrier feedback loop are the strongest non-reproducible moat.
Keep them private, or release only deliberately curated and privacy-reviewed
insights.

| Area | Assessment | Publication action |
|---|---|---|
| Python/TypeScript/JavaScript source | Apache-2.0 project code | Ready after normal owner review |
| Example configuration and scenario metadata | Generic, credential-free, reserved numbers | Ready |
| Clean-room segmented PBX scenario/text | Simulator-authored text and synthesized runtime tone | Ready; local WAV renders remain excluded |
| Synthetic WAV corpus | Manifested locally, but exact redistribution permission is unresolved | Excluded from Git, wheels, web, and Docker by default |
| Generated IVR MP3 output | Ignored; manifest records engine/model/voice fingerprint/checksum | Do not publish automatically |
| Device/carrier captures | Potential consent, contract, trademark, and recording-law concerns | Keep private unless explicitly cleared |
| Vendor/product names | Descriptive compatibility references | Keep NOTICE disclaimer; avoid logos or affiliation claims |

## What was removed or neutralized

- No `.env` file, API credential, auth token, account SID, private bucket,
  tenant ID, customer email, VIN, or company-specific UAT payload is included.
- The former fixed UAT runner was replaced by an adapter pipeline whose
  secrets come from environment variables.
- The former Asterisk recording fixture was not carried forward. A clean-room
  scenario preserves the segmented-prompt, beep timing, and overlap behavior.
- Personal names were replaced with fictional example identities.
- The former live IVR destination was replaced by a reserved 555 example.
- Full-call recording is disabled by default. API/console deletion removes
  simulator-managed records and local artifacts; provider-hosted recordings
  remain subject to separate provider retention or deletion.

## Audio publication gate

`corpus/assets_manifest.json` is enforced by tests. Every local source WAV has:

- a source type and source reference;
- a license or permission note;
- a transcript source and voice hint;
- an explicit `commit_allowed` decision.

That metadata is necessary but does not create redistribution rights.
`corpus/public_release_allowlist.json` is intentionally empty, and every
unresolved manifest row has `commit_allowed: false`. The default public
repository, wheel, web export, and Docker contexts therefore contain no corpus
WAV or MP3 binary.

For generated files, preserve functionality while strengthening provenance
with one of these release choices:

1. Regenerate every WAV with a TTS provider/voice whose output terms the
   repository owner has reviewed, then record engine, model, voice, account
   owner, generation date, and checksum.
2. Publish code, scenario YAML, and clean-room text without the uncertain
   binaries; require contributors to generate their local corpus.
3. Keep a private corpus package and publish only assets individually approved
   for redistribution.

Do not assume that publicly audible carrier or assistant audio is
redistributable. Do not clone a recognizable vendor voice. Prefer
simulator-authored paraphrases and voices the project is licensed to use.

## Secret and privacy boundary

- Provider credentials remain environment variables. They are not seeded or
  exposed in the console.
- Recordings are opt-in and path-confined. Their API and the console must stay
  on private ingress. Simulator-owned
  copies are deletable locally; provider-hosted copies require provider-side
  retention or deletion.
- Generic UAT reports redact adapter output by default; artifacts and verbose
  output require explicit operator configuration.

Before a public release, run a repository secret scanner and dependency/license
scanner in the publishing organization as an independent control in addition
to the built-in tests. The built-in `pnpm license:check` verifies dependency
license metadata; `pnpm public-release:check` verifies the audio boundary and
rejects private configuration, database files, credentials, recordings,
captures, backups, and exports from the Git publication set. The check
includes non-ignored untracked files, so it is effective before the initial
commit.

CI also audits the shipped web dependency graph at the critical threshold.
The ESLint-only graph currently retains a high-severity brace-expansion denial
of service advisory because its compatible minimatch branch has no patched
release. It is not bundled into the static console or nginx runtime. Revisit
that exception when the lint toolchain exposes a compatible patched version.
The optional pinned `twilio-run` deployment tool likewise has known
high/moderate advisories but no critical advisory; run it only on a constrained
operator workstation and migrate to supported Twilio CLI tooling when
practical.
