# Configuration reference

The backend reads process environment variables. It does not require a
particular secret manager. For local use, either export a reviewed `.env` file
or pass it explicitly:

```bash
cd apps/simulator-backend
uv run --env-file .env telephony-voice-sim serve --with-pstn
```

Never commit `.env` files. `apps/simulator-backend/.env.example` is the
canonical inventory.

## Control plane

| Variable | Default | Purpose |
|---|---:|---|
| `SIMULATOR_BIND_HOST` | `127.0.0.1` | API listen address |
| `SIMULATOR_BIND_PORT` | `8978` | API listen port |
| `SIMULATOR_WEB_ORIGINS` | localhost ports | Comma-separated browser origins allowed by CORS |
| `SIMULATOR_RUNTIME_DIR` | source app or `./.telephony-voice-simulator` | Writable root for installed/runtime state |
| `SIMULATOR_DATA_DIR` | `<runtime>/data` | Override SQLite and other mutable data |
| `SIMULATOR_RESULTS_DIR` | `<runtime>/results` | Override call and grading artifacts |
| `SIMULATOR_COMPILED_ASSETS_DIR` | `<data>/compiled-assets` | Override generated PSTN prompt segments |
| `SIMULATOR_DATABASE_URI` | empty | Preferred database selector: `sqlite://` or `mysql+pymysql://` |
| `SIMULATOR_DB_PATH` | package data directory | Legacy SQLite path when no database URI is set |
| `SIMULATOR_CORPUS_ASSETS_DIR` | source checkout assets or runtime data | Private/local source WAV directory |
| `SIMULATOR_CORPUS_MANIFEST` | beside the selected assets directory | Matching private/local provenance manifest |
| `SIMULATOR_PUBLIC_AUDIO_ALLOWLIST` | packaged empty allowlist | Explicit WAV filenames approved for public PSTN serving |
| `PSTN_AUDIO_DEPLOYMENT_ALLOWLIST` | `SIMULATOR_PUBLIC_AUDIO_ALLOWLIST` | Preferred per-deployment override for the reviewed public AMD allowlist |
The control API has no application-level authentication. Keep it on loopback
or private authenticated ingress and never publish `/api/*` with carrier
webhook routes.

### Persistence

SQLite remains the zero-configuration default. For a durable/shared MySQL
deployment, create the database and give the application user permission to
create and update its tables, then set:

```bash
SIMULATOR_DATABASE_URI='mysql+pymysql://simulator:password@mysql:3306/telephony_voice_simulator'
```

The backend creates its schema on startup. `mysql://` and
`mysql+pymysql://` are equivalent. Use percent-encoding for reserved
characters in credentials. An explicit `SimulatorStore(path)` in embedded
code overrides the environment; otherwise `SIMULATOR_DATABASE_URI` takes
precedence over `SIMULATOR_DB_PATH`. Database URIs may contain credentials and
must be handled as secrets.

For local development, the optional Compose overlay starts MySQL on loopback
and configures the containerized backend to use it:

```bash
docker compose -f compose.yaml -f compose.mysql.yaml up -d mysql
# or start the complete stack:
docker compose -f compose.yaml -f compose.mysql.yaml up --build
```

The defaults (`simulator` / `simulator-local`) are intentionally local-only.
Override `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`,
`MYSQL_ROOT_PASSWORD`, and `SIMULATOR_MYSQL_URI` for any shared environment.
Do not commit those values.

## Twilio and PSTN

| Variable | Default | Purpose |
|---|---:|---|
| `PUBLIC_BASE_URL` | none | Public HTTPS origin used in TwiML callbacks and assets |
| `TWILIO_ACCOUNT_SID` | none | Twilio account identifier |
| `TWILIO_AUTH_TOKEN` | none | Twilio API credential and webhook-signature secret |
| `TWILIO_FROM_NUMBER` | none | Owned source number for hosted smoke calls |
| `IVR_CALLER_ID` | inbound Twilio number | Caller ID for the IVR's bridged outbound leg |
| `IVR_MAX_ATTEMPTS` | `3` | IVR input attempts, clamped to a safe maximum |
| `PSTN_DEFAULT_SCENARIO` | none | Fallback AMD scenario for unqueued calls |
| `PSTN_NUMBER_MAP` | `{}` | JSON map of owned inbound numbers to AMD scenario names |
| `BRIDGE_NUMBER` | none | Human destination for scenarios whose dial target is `bridge` |
| `PSTN_RECORD_ALL_CALLS` | `false` | Start a dual-channel full-call recording |
| `PSTN_ALLOW_UNSIGNED_WEBHOOKS` | `false` | Local-only override; never use on a public endpoint |
| `PSTN_ACKNOWLEDGE_PUBLIC_AUDIO` | `false` | Required acknowledgement before non-loopback AMD media serving |
| `PSTN_BIND_HOST` | `127.0.0.1` | Bind host for the historical standalone PSTN module |
| `PSTN_BIND_PORT` | `8978` | Port for the standalone PSTN module |

Twilio credentials and `PUBLIC_BASE_URL` are required for real PSTN calls.
Recording remains off unless explicitly enabled. Operators must establish
notice, consent, retention, access, and deletion controls before enabling it.
Use `serve --with-ivr` when only the four-digit directory is required; it does
not compile or expose AMD audio. Public `serve --with-pstn` additionally
requires the audio acknowledgement, a deployment allowlist, and approved
manifest checksums for every source asset referenced by an AMD scenario.
Generated WAVs are served from intentionally unauthenticated `/assets/` URLs
so Twilio can fetch them; the deployment allowlist is therefore a
public-serving decision, not merely permission to mount a private file.

## LiveKit voice-agent adapter

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL` | LiveKit server URL |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit API credentials |
| `AGENT_NAME` | Deployed agent worker name |
| `METADATA_TEMPLATE` | Outbound dispatch metadata template |
| `SIM_NUMBER` | Public simulator number the agent should call |
| `SIM_CALLER_ID` | Account-owned caller ID used by the agent's SIP trunk |
| `AGENT_WEBHOOK_URL` | Optional agent lifecycle webhook |

The in-process scenario runner additionally uses `WEBHOOK_BIND_HOST`,
`WEBHOOK_BIND_PORT`, and optional `WEBHOOK_PUBLIC_URL`.

## Grading, corpus, and prompt generation

| Variable | Purpose |
|---|---|
| `EXPECTED_VOICEMAIL_MESSAGE` | Override the portable fixture during hosted grading |
| `UAT_EXPECTED_VOICEMAIL_MESSAGE` | Backward-compatible alias for the same override |
| `RECORDINGS_BUCKET` | Operator-owned bucket used by the optional recording importer |
| `OPENAI_API_KEY` | Optional analysis, corpus TTS, or IVR prompt TTS |
| `OPENAI_TTS_MODEL` / `OPENAI_TTS_VOICE` | IVR prompt generation choices |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | Optional IVR prompt generation |
| `ELEVENLABS_MODEL_ID` | ElevenLabs model, default `eleven_v3` |
| `ELEVENLABS_SEED` | Best-effort deterministic generation seed, default `42` |
| `ELEVENLABS_VOICE_SETTINGS_JSON` | Optional JSON object of provider voice settings; v3 defaults to Robust stability (`1.0`), neutral style (`0`), and normal speed (`1.0`) |
| `MACOS_TTS_VOICE` | Local macOS `say` voice fallback |
| `SIMULATOR_INCLUDE_LOCAL_AUDIO` | Local-only web audio export opt-in; default `false` |
| `TELNYX_API_KEY` | Signals preview Telnyx configuration; live execution is not implemented |

TTS-generated output is not automatically safe to redistribute. Check the
provider's terms and rights to the chosen voice. The local AMD corpus has
separate per-asset provenance metadata and is excluded from public artifacts.

## Web console

| Variable | Default | Purpose |
|---|---:|---|
| `NEXT_PUBLIC_SIMULATOR_API_URL` | `http://127.0.0.1:8978` | API origin baked into the static console |

Because the console is a static export, rebuild it whenever its API origin
changes.

`SIMULATOR_INCLUDE_LOCAL_AUDIO=true` allows the content generator to export
local corpus files and composite scenario previews into a local static console
build. Composite previews preserve prompt timing and synthesized tones while
shortening long response/recording windows. The option is intentionally off by
default and must not be set in public builds.

ElevenLabs can render both the optional IVR prompts and the clean-room AMD
scenario corpus. Keep `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in the
gitignored `apps/simulator-backend/.env`, then run:

```bash
cd apps/simulator-backend
uv run --env-file .env python -m telephony_voice_simulator.corpus.build_corpus
```

Existing corpus files remain cached. Use `--force` only when intentionally
replacing generated assets; imported or captured assets still require the
additional `--replace-imported` safeguard.
