# Contributing

Thank you for improving Telephony Voice Simulator.

## Development setup

Prerequisites are Python 3.11 or newer, `uv`, Node.js 22, pnpm 9, and `ffmpeg`.

```bash
pnpm install
cd apps/simulator-backend
uv sync --frozen
cd ../..
pnpm lint
pnpm test
pnpm build
pnpm license:check
pnpm public-release:check
```

Keep provider-specific behavior behind an adapter. AMD, IVR, and PBX scenarios
should continue to use the shared catalog and timeline vocabulary rather than
introducing another control plane or database.

## Audio and scenario contributions

Every corpus entry must have a matching `assets_manifest.json` row containing
its origin, transcript source, licensing note, and an explicit
`commit_allowed` decision.

Audio imports and generated files are ignored and private by default. Do not
set `commit_allowed: true` or add a filename to
`public_release_allowlist.json` without a documented maintainer release
review. The allowlist, exact-file ignore exceptions, checksum, and package
configuration must change together.

Do not contribute:

- Audio copied from a PBX, carrier, handset, customer call, or commercial sound
  pack without documented redistribution rights
- A cloned or impersonated person's voice
- Production recordings containing personal information or call content
- Customer identifiers, account numbers, internal endpoints, buckets, tokens,
  or proprietary test payloads

Prefer clean-room prompt text and synthesized tones. Generated voice assets
must also comply with the selected TTS provider's terms. Run:

```bash
cd apps/simulator-backend
uv run pytest tests/test_scenarios_lint.py
```

## Pull requests

Keep changes focused, add tests for behavior changes, and explain deployment or
privacy effects. Confirm that lint, backend tests, prompt/UAT configuration
checks, and the production web build pass.

By submitting a contribution, you agree that it is licensed under Apache-2.0
and that you have the right to submit it.
