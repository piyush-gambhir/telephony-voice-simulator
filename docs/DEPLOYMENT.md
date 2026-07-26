# Deployment

## Local development

The default development command starts the API and web console:

```bash
pnpm install
cd apps/simulator-backend && uv sync --frozen && cd ../..
pnpm dev
```

Start the combined Twilio runtime only after setting the Twilio webhook
credential and public URL:

```bash
pnpm dev:pstn
```

The console is at `http://127.0.0.1:3000`; the backend health endpoint is
`http://127.0.0.1:8978/health`.

## Containers

Start the loopback-only API and static console:

```bash
docker compose up --build
```

The default Compose file binds both ports to `127.0.0.1`. Set
`SIMULATOR_LISTEN_ADDRESS` or `WEB_LISTEN_ADDRESS` only when a trusted reverse
proxy or network policy is in place. SQLite is stored in the named
`simulator-data` volume by default. For external MySQL, set
`SIMULATOR_DATABASE_URI=mysql+pymysql://user:password@host:3306/database`;
the database must already exist and the application user must be able to
create/update its schema.

The development MySQL overlay is loopback-only and uses a named volume:

```bash
docker compose -f compose.yaml -f compose.mysql.yaml up -d mysql
docker compose -f compose.yaml -f compose.mysql.yaml up --build
```

The first command starts only MySQL for host-run development. The second starts
the whole container stack with the backend connected to MySQL. Override the
development-only `MYSQL_*` defaults and `SIMULATOR_MYSQL_URI` in an ignored
`.env` file before using the overlay on a shared machine.

The published runtime images intentionally contain no corpus audio or
repository operations scripts. Generate/import a private corpus from a source
checkout, outside the image:

```bash
export SIMULATOR_PRIVATE_CORPUS_DIR="$PWD/.local/private-corpus"
mkdir -p "$SIMULATOR_PRIVATE_CORPUS_DIR/assets"
cd apps/simulator-backend
SIMULATOR_CORPUS_ASSETS_DIR="$SIMULATOR_PRIVATE_CORPUS_DIR/assets" \
SIMULATOR_CORPUS_MANIFEST="$SIMULATOR_PRIVATE_CORPUS_DIR/assets_manifest.json" \
uv run python -m telephony_voice_simulator.corpus.build_corpus
cd ../..
```

The generated manifest keeps every binary private by default. `.local/` is
ignored by Git. The PSTN Compose overlay requires the absolute
`SIMULATOR_PRIVATE_CORPUS_DIR` and mounts that directory read-only; the
container reads `assets/`, its matching `assets_manifest.json`, and a
separately reviewed `public_audio_allowlist.json`.

## Public PSTN deployment

Choose the narrowest live mode:

- `compose.ivr.yaml` serves the shared IVR without loading or exposing AMD
  audio.
- `compose.pstn.yaml` serves IVR and AMD. It compiles the private source corpus
  and exposes generated WAVs through unauthenticated `/assets/` URLs because
  Twilio must be able to fetch them.

For public AMD, review every source WAV referenced by every loaded AMD
scenario. A private mount protects the sources at rest; it does not make the
compiled HTTP assets private. Never use customer calls, real voicemail
captures, personal data, or audio that is not approved for unauthenticated
public serving.

Create `public_audio_allowlist.json` in the private corpus directory:

```json
{
  "version": 1,
  "assets": [
    "rights-reviewed-greeting.wav"
  ]
}
```

This private deployment list is separate from the repository's
`public_release_allowlist.json`. Its entries require matching source/license
provenance and SHA-256 values, but they do not set `commit_allowed: true` and
do not permit committing the WAVs. The list must contain every `play` asset in
the installed AMD scenario catalog, not only the selected default. Validate
the review set before deployment:

```bash
cd apps/simulator-backend
uv run python scripts/validate_public_audio.py \
  --allowlist /absolute/path/to/private-corpus/public_audio_allowlist.json \
  --assets-dir /absolute/path/to/private-corpus/assets \
  --manifest /absolute/path/to/private-corpus/assets_manifest.json
cd ../..
```

The server also needs a public HTTPS origin for Twilio. Keep the console and
`/api/*` private; public ingress must forward only `/twilio/*` and the reviewed
AMD `/assets/*` paths. Configure:

- `PUBLIC_BASE_URL`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- Any optional number map, caller ID, or bridge destination

Then apply the PSTN overlay:

```bash
export SIMULATOR_PRIVATE_CORPUS_DIR=/absolute/path/to/rights-reviewed-corpus
export PSTN_ACKNOWLEDGE_PUBLIC_AUDIO=true
docker compose -f compose.yaml -f compose.pstn.yaml up --build
```

For the shared four-digit IVR without AMD audio, use the IVR-only overlay:

```bash
docker compose -f compose.yaml -f compose.ivr.yaml up --build
```

Terminate TLS at a trusted reverse proxy and forward to backend port 8978.
Apply request-size and rate limits. Keep
`PSTN_ALLOW_UNSIGNED_WEBHOOKS=false`. Do not publish database access or the
recording directory. The acknowledgement is not an access control: the
runtime still verifies the deployment allowlist, provenance, files, and
checksums before binding.

Deleting a call through the API or console permanently removes only the
simulator-managed database rows, in-memory state, and local artifact files. It
does not call Twilio to delete provider-hosted `RecordingSid` objects.
Configure Twilio retention or delete those provider recordings separately
when full erasure is required.

Attach one owned number to the unified IVR:

```bash
cd apps/simulator-backend
uv run python scripts/twilio_number.py attach \
  --number +15550101000 \
  --base-url https://simulator.example.test \
  --route ivr \
  --status-callback-url https://simulator.example.test/twilio/status
```

Use `--route amd` for `/twilio/voice`. The tool changes only the exact number
supplied and saves its previous Twilio configuration. Restore it with:

```bash
uv run python scripts/twilio_number.py restore \
  --number +15550101000 \
  --backup results/twilio/PNxxxxxxxx.json
```

Twilio supports one Voice URL per number. A number cannot serve `/twilio/voice`
and `/twilio/ivr` at the same time; provision separate numbers or use the
backup-first attach/restore commands to switch it deliberately. Deleting the
local endpoint does not detach the carrier number.

The hosted AMD deployer makes compiled WAVs public Twilio Assets and therefore
requires its own explicit acknowledgement and private deployment allowlist:

```bash
cd apps/simulator-backend
uv run python -m telephony_voice_simulator.pstn.deploy_twilio \
  --acknowledge-public-audio \
  --audio-deployment-allowlist /absolute/path/to/deployment_allowlist.json
```

It prints a Serverless machine URL but deliberately does not modify phone
numbers. Attach that URL through the same backup-first tool:

```bash
uv run python scripts/twilio_number.py attach \
  --number +15550101000 \
  --voice-url 'https://your-service.twil.io/machine?mode=voice'
```

Hosted `/machine` stores its queue and call documents in a separate Twilio Sync
service. Its controls are the deployment and grading CLIs; the web console
controls the unified backend queue and does not enqueue hosted calls.

## Standalone Twilio Functions IVR

`deploy/twilio-ivr` restores the optional `twilio-run` workflow:

```bash
cd deploy/twilio-ivr
cp .env.example .env
npm ci
npm test
npm run license:check
npm run audit:ci
npm run start
npm run deploy
```

Its environment-backed directory is deliberately separate from the shared repository. Use it
only when a fully Twilio-hosted, independently configured IVR is wanted. For a
directory shared with the console, use the unified backend.

`twilio-run` is pinned in `package-lock.json` as local deployment tooling. CI
fails on critical advisories and scans its complete installed license graph;
review high/moderate tooling advisories before using it on a privileged
workstation.

The Function uses Twilio `<Say>` by default. Optional clean-room prompt assets
can be generated with `npm run prompts`, reviewed using `assets/manifest.json`,
and enabled with `USE_PROMPT_ASSETS=true`.

## Static console

The web console exports to `apps/web-console/out`. It may be hosted on any
static platform:

```bash
NEXT_PUBLIC_SIMULATOR_API_URL=https://simulator.example.test pnpm build
```

The API must remain private and allow the deployed console origin through
`SIMULATOR_WEB_ORIGINS`.

## Release checklist

- All checks in CI pass.
- The public deployment uses HTTPS and does not expose `/api/*`.
- Twilio webhook signature validation is enabled.
- Call recording is off unless a reviewed policy requires it.
- Real destinations, credentials, and customer data are absent from the source
  tree.
- `public_release_allowlist.json` contains only checksum-bound,
  rights-reviewed files; it is empty for the initial release.
- Public AMD is disabled or its distinct deployment allowlist covers every
  referenced asset and `scripts/validate_public_audio.py` passes.
- `pnpm license:check` and `pnpm public-release:check` pass, including wheel
  and web artifact checks.
- A rollback backup exists before changing any Twilio number.
