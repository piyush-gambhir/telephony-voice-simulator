# Simulator backend

The Python backend is the canonical runtime for Telephony Voice Simulator. It
owns scenario discovery, provider adapters, local execution, PSTN webhooks,
recordings, grading, persistence, the CLI, and the HTTP API.

## Start the API

```bash
uv sync
uv run telephony-voice-sim serve
```

Use `--with-ivr` for the live directory without any AMD corpus:

```bash
uv run telephony-voice-sim serve --with-ivr
```

Use `--with-pstn` to mount both AMD and IVR Twilio runtimes. AMD startup
requires a rights-reviewed private corpus. Public serving additionally
requires `PSTN_ACKNOWLEDGE_PUBLIC_AUDIO=true`, an explicit deployment
allowlist, and matching approved manifest checksums:

```bash
cp .env.example .env
PUBLIC_BASE_URL=https://your-tunnel.example \
TWILIO_ACCOUNT_SID=... \
TWILIO_AUTH_TOKEN=... \
uv run telephony-voice-sim serve --with-pstn
```

The control API has no application-level login. Bind it to loopback or a
private network and do not forward `/api/*` through the public carrier ingress.
Only Twilio webhooks and the exact active set of approved compiled audio assets
should be internet-reachable. Webhook signatures and the startup audio policy
remain their respective boundaries.

Twilio webhook signatures are verified whenever PSTN mode is enabled. Unsigned
webhooks are rejected unless `PSTN_ALLOW_UNSIGNED_WEBHOOKS=true` is explicitly
set for local-only testing. Recording downloads are constructed from validated
Twilio recording SIDs; callback-supplied URLs are never fetched with account
credentials.

Configure a Twilio number with one of these POST webhooks:

- `/twilio/voice` for queued AMD/callee scenarios.
- `/twilio/ivr` for the live extension menu. It gathers four DTMF digits,
  resolves the same directory managed by `/api/directory` and the web console,
  then bridges the call. Its dial-result callback persists the selected digits,
  destination, child-leg result, and status timeline in call history. Set
  `IVR_CALLER_ID` when the outbound leg should use a caller ID other than the
  inbound Twilio number.

A Twilio number has one Voice URL. It cannot target AMD and IVR simultaneously:
use separate numbers, or explicitly reattach the number when changing modes.
`PSTN_NUMBER_MAP` is a legacy fallback only for numbers that have never been
managed through the endpoint control plane.

## Local workflow

```bash
uv run telephony-voice-sim add-provider mock "Local simulator"
uv run telephony-voice-sim providers
uv run telephony-voice-sim scenarios
uv run telephony-voice-sim add-endpoint <provider-id> "Test line" 1001 \
  --kind extension --scenario ivr_connect_extension
uv run telephony-voice-sim add-extension <provider-id> 1501 "Front desk" +15550101501 \
  --department Reception --timeout 25
uv run telephony-voice-sim directory
uv run telephony-voice-sim run <endpoint-id> ivr_connect_extension
uv run telephony-voice-sim simulate-ivr <endpoint-id> +14085550131 1501 \
  --disposition busy
uv run telephony-voice-sim runs
uv run telephony-voice-sim calls
```

The installed `amd-sim` and `telephony-sim` commands are compatibility aliases
for `telephony-voice-sim`. The deprecated `POST /api/simulations` route accepts
the original camelCase IVR request and returns its `{run, steps}` shape with a
deprecation header; new clients should use `POST /api/ivr/simulations`.

Persistence defaults to SQLite. Set
`SIMULATOR_DATABASE_URI=mysql+pymysql://user:password@host:3306/database` to
use MySQL; the URI takes precedence over the legacy `SIMULATOR_DB_PATH`.
`sqlite:///:memory:` creates an isolated in-memory store for embedded use and
tests. Call `store.close()` when finished with an embedded store.

Inspect an individual run with `GET /api/runs/{run_id}`. Cancel an assignment
before its incoming call arrives with `POST /api/runs/{run_id}/cancel` or
`telephony-voice-sim cancel-run <run-id>`. Cancellation removes only that
assignment, preserves its history with status `cancelled`, and is idempotent.
If a call has already claimed the assignment, the API returns `409` and leaves
the active call untouched.

Validate authored AMD scenarios without opening a database or starting calls:

```bash
uv run telephony-voice-sim validate --all
uv run telephony-voice-sim validate --scenario stock_voicemail_beep_1000
uv run telephony-voice-sim validate --scenario /path/to/custom.yaml
uv run telephony-voice-sim validate --all --scenarios-dir /path/to/scenarios
```

Validation reports each file's result, rejects duplicate names, and exits with
status `1` if any file is invalid. It checks the authored scenario structure;
audio release approval and agent behavior remain separate checks.

Local call deletion is available through `DELETE /api/calls/{call_id}` in the
control API and from the console. It removes database metadata and only the exact
simulator-owned call artifact directory; external and symlinked paths are
never followed. It does not delete a provider-hosted Twilio `RecordingSid`;
configure provider retention or delete that copy through Twilio separately.

Deleting an endpoint does not detach or reconfigure its carrier number. The
backend retains a local ownership tombstone and rejects that number until it is
explicitly re-created; use `scripts/twilio_number.py` to attach or restore the
carrier Voice URL.

## Advanced AMD workflows

The migrated operational modules are executable entry points rather than dead
library code:

```bash
# Run one or every AMD scenario against a LiveKit room.
uv run python -m telephony_voice_simulator.runner --scenario stock_voicemail_beep_1000
uv run python -m telephony_voice_simulator.runner --all

# Dispatch a deployed agent to a queued PSTN scenario.
uv run python -m telephony_voice_simulator.agent_call \
  --scenario stock_voicemail_beep_1000

# Build/deploy the always-on Twilio Serverless AMD runtime. Its generated WAVs
# are public Twilio Assets, so an explicit private deployment approval is
# mandatory.
uv run python -m telephony_voice_simulator.pstn.deploy_twilio \
  --acknowledge-public-audio \
  --audio-deployment-allowlist /absolute/path/to/deployment_allowlist.json

# Build or import rights-reviewed corpus audio.
uv run python -m telephony_voice_simulator.corpus.build_corpus
uv run python -m telephony_voice_simulator.corpus.import_asset --help
```

The hosted `/machine` runtime uses its own Twilio Sync queue. It is controlled
by the deployment/grading CLI and is not controlled by the web console or the
unified backend queue.

See the root UAT and deployment documentation for the hosted grading and
multi-step evidence pipeline.

## Layout

```text
src/telephony_voice_simulator/
├── control/      API, CLI, application service, and SQLite/MySQL repositories
├── telephony/    Common provider contract plus isolated Twilio/Telnyx modules
├── domains/      IVR and PBX scenario packs on the shared timeline model
├── scenarios/    AMD scenario YAML
├── corpus/       Audio generation, imports, provenance, and assets
├── pstn/         Provider-neutral AMD compilation and analysis; compatibility entry points
├── templates/    Bundled provider-neutral outbound metadata template
├── paths.py      Canonical package and writable runtime locations
├── machine.py    In-process answering-machine state machine
└── assertions.py AMD timing and content checks
```

The local simulator executes every scenario domain. Twilio executes AMD callee
scenarios and the shared live IVR; PBX scenarios remain deterministic models.

Provider descriptors expose `supported_scenario_kinds` alongside endpoint kinds
and capabilities. Clients should use that catalog instead of hardcoding carrier
names. To add a provider, implement `telephony.base.ProviderAdapter`, declare its
supported domains, validate connection settings, and implement `dispatch`.
Register it in `telephony.registry.provider_registry`, or inject a complete
mapping with `SimulatorService(providers={"custom": adapter}, store=store)` for
an embedded application. Connection validation runs before persistence; adapters
use the `ProviderRuntimeStore` port for durable queues rather than database SQL.

Free-form IVR durations measure simulated time through the last timeline event:
menu handling plus the configured ring timeout for unanswered calls, or time
until the bridge for answered calls. They do not estimate conversation length.

## Tests

```bash
uv run pytest
uv run ruff check .
```

Full-call recording defaults to off. Call recording laws and consent
requirements vary by jurisdiction. Enable `PSTN_RECORD_ALL_CALLS=true` only
where required notice, consent, retention, access, and deletion controls are in
place.
