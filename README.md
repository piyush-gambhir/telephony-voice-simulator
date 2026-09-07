# Telephony Voice Simulator

A local-first monorepo for testing the complete voice-call boundary: answering
machine detection (AMD), inbound IVRs, PBX routing, extensions, queues,
screeners, human pickup, live carrier behavior, recordings, and grading.

This is the merged successor to the former AMD and telephony simulators. AMD
is now one scenario domain on top of a shared telephony platform rather than a
second application with duplicate storage, provider, and UI layers.

## Repository structure

```text
telephony-voice-simulator/
├── apps/
│   ├── simulator-backend/   Python API, CLI, SQLite/MySQL, scenario domains,
│   │                        LiveKit/Twilio adapters, grading, and recordings
│   └── web-console/         Borderless static Next.js operator console
├── deploy/
│   ├── nginx.conf           Hardened static-console container config
│   └── twilio-ivr/          Optional standalone Twilio Functions IVR
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ASSET_POLICY.md
│   ├── CONFIGURATION.md
│   ├── DEPLOYMENT.md
│   ├── HLD.md
│   ├── MIGRATION_AUDIT.md
│   ├── OPEN_SOURCE_AUDIT.md
│   └── UAT.md
├── scripts/
│   └── generate_content.py  Backend catalog → static scenario reference
├── compose.yaml
├── compose.pstn.yaml
└── pnpm-workspace.yaml
```

## What is included

The catalog currently contains 43 scenarios:

- 32 AMD/callee-machine scenarios covering voicemail, unusual beeps,
  multipart and no-beep greetings, screeners, DTMF gates, dead ends, multiple
  languages, human-answer guards, and a clean-room segmented PBX voicemail.
- 7 IVR scenarios covering valid routing, invalid input, retry, silence, busy,
  no-answer, carrier failure, and abandonment.
- 4 PBX scenarios covering schedules, direct extensions, queues, fallback,
  overflow, voicemail, CRM activity, and CDR completion.

The backend also includes:

- Provider profiles for the local simulator, Twilio, and the Telnyx preview adapter.
- Endpoint and extension-directory CRUD with enable/disable controls.
- Persisted normalized runs and caller/destination/outcome analytics.
- A shared live Twilio IVR backed by the same repository as the console.
- A live Twilio AMD callee runtime, DTMF relay, call capture, grading, and an
  optional Twilio Serverless deployment.
- LiveKit room-mode and deployed-agent runners.
- A vendor-neutral REST agent adapter that asks the agent's own backend to
  place the call, plus an AMD benchmark suite that scores the agent over
  repeats and reports a machine/human/screener confusion matrix.
- Generic multi-step UAT orchestration with retries, captured values, grading,
  and artifact collection.
- Opt-in recordings with private-control-plane playback and deletion of
  simulator-managed metadata and local artifacts.
- A dealer-group PBX model (`pbxsim`) with a declarative three-rooftop
  configuration on legacy on-prem switches, a deterministic router, agent
  transfer evaluation, and 30 cases. See
  [`docs/DEALER_GROUP_PBX.md`](docs/DEALER_GROUP_PBX.md) and the `dealer-map`,
  `dealer-call`, `dealer-case`, and `dealer-suite` CLI commands.

PBX is a deterministic routing model, not a production SIP registrar. Telnyx
configuration is visible as a preview; live Telnyx Call Control execution is
not represented as complete.

## Run locally

Requirements are Node.js 22+, pnpm 9, Python 3.11+, `uv`, and `ffmpeg`.

```bash
pnpm install
cd apps/simulator-backend
uv sync --frozen
cd ../..
pnpm dev
```

The API listens on `http://127.0.0.1:8978`. Open the console at
`http://127.0.0.1:3000/console` and create the provider connections, numbers,
and routes required for your workspace.

SQLite is the default. Set `SIMULATOR_DATABASE_URI` to a
`mysql+pymysql://user:password@host:3306/database` URI when durable/shared
MySQL persistence is required.

For a loopback-only local MySQL instance:

```bash
docker compose -f compose.yaml -f compose.mysql.yaml up -d mysql
```

The overlay uses development-only defaults and stores data in the
`simulator-mysql-data` volume. Use `SIMULATOR_MYSQL_URI` and the matching
`MYSQL_*` variables to override them.

For temporary carrier testing, start the narrow public-ingress proxy and point
ngrok at port `8980`:

```bash
docker compose -f compose.yaml -f compose.ingress.yaml up -d public-ingress
ngrok http http://127.0.0.1:8980
```

The ingress exposes only `/twilio/*`, approved `/assets/*`, and `/health`.
It deliberately returns `404` for `/api/*`; never point ngrok directly at the
private backend port.

The control API has no application-level login. Keep it on loopback or a
private network and expose only signed provider webhook and reviewed media
routes through public ingress.

Useful commands:

```bash
pnpm lint
pnpm test
pnpm scenarios:check
pnpm build
pnpm uat:check
pnpm prompts:check
pnpm license:check
pnpm public-release:check

cd apps/simulator-backend
uv run telephony-voice-sim scenarios
uv run telephony-voice-sim sim --kind ivr
uv run telephony-voice-sim sim --kind pbx
```

Start with the [high-level design](docs/HLD.md) for the complete system and
end-to-end call flows. See [Deployment](docs/DEPLOYMENT.md) for containers and live Twilio setup,
[Configuration](docs/CONFIGURATION.md) for every environment variable, and
[UAT](docs/UAT.md) for portable end-to-end agent testing, and
[Benchmark](docs/BENCHMARK.md) for scoring an agent's answering-machine
detection over repeats.

See [Extending the simulator](docs/EXTENDING.md) for adding private scenario
packs, provider adapters, PBX models, and console features. The console exposes
provider, endpoint, directory, and local simulation workflows, with automatic
refresh, searchable run/call history, JSON export, and queued-run cancellation.
The [project review](docs/PROJECT_REVIEW.md) records the improvements,
verification, and remaining integration boundaries.

## Migration and open-source status

The feature-by-feature migration, replacements, and retained command-line
entry points are documented in
[Migration audit](docs/MIGRATION_AUDIT.md). The duplicate Next.js server API,
Drizzle/PostgreSQL layer, old simulator engines, and duplicate provisioning
script were intentionally removed after their behavior moved into the shared
backend.

The code is Apache-2.0 licensed and has been stripped of credentials,
tenant-specific payloads, real operational destinations, and the restricted
Asterisk audio fixture. The clean-room replacement preserves its timing and
overlap assertions without vendor recordings.

Public Git, wheel, web, and Docker builds exclude all corpus WAV/MP3 binaries
by default. The scenario definitions and clean-room fixture text are suitable
for publication; local synthetic speech remains behind the initially empty
per-file release allowlist described in
[Open-source audit](docs/OPEN_SOURCE_AUDIT.md). Vendor names describe
compatibility only and do not imply affiliation.

Full-call recording is configured per managed number in the console and is off
for newly imported numbers by default. AMD scenarios with a record window save
the isolated post-beep message separately for grading. Only enable complete-call
capture after establishing the notice, consent, access, retention, and deletion
rules required for every jurisdiction involved. Deleting a call in the console
removes the simulator's database records and local files; it does not remotely
erase a provider-hosted Twilio `RecordingSid`. Configure provider retention or
delete those recordings in Twilio separately.
