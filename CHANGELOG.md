# Changelog

## Unreleased

### Added

- **Transfer testing.** Five `transfer_target` scenarios answer the line a voice agent
  transfers a caller to: warm accept, assistant accept, cold accept, decline and busy.
  The `transfer-to-sales` and `transfer-to-sales-unreachable` personas ask for sales on
  the customer line, and code words heard on each leg prove the bridge carried audio
  both ways. See `docs/TRANSFERS.md`.
- `reject: busy|rejected` scenario step turns a call away without answering.
- `transcript_contains` and `transcript_excludes` checks grade every recording of a call.
- `customer dial` has a persona line call an agent's inbound number.

### Fixed

- Enabling or disabling a managed number without sending a scenario no longer fails
  with "Unknown scenario".
- Deleting a call also strips its analysis (transcripts, numbers) from the run it
  finished; the run keeps only its verdict and summary.
- `numbers sync` adopts a number already added as a line on another Twilio connection
  instead of creating a second line for it.
- `numbers point` keeps a separate backup for every change, so pointing a number again
  no longer overwrites the configuration from before the first change.
- A benchmark attempt whose trigger the agent rejects (a refused destination or a 4xx)
  cancels its queued scenario, so the line's next call does not inherit it.
- UAT: a missing command or working directory fails the step and still runs cleanup
  steps and the report; captured values parse the whole response, not the first 12,000
  characters.
- The PBX API honors a patience of zero seconds instead of replacing it with 600.
- Results shows synthetic-customer grades: each persona expectation and check counts as
  a check, so Results and the Inbox agree.
- Phone and SIP lines on the local mock provider must use E.164 numbers and sip: URIs,
  like the Twilio and Telnyx providers.
- Removed the unused `PSTN_BIND_HOST` and `PSTN_BIND_PORT` settings and the outdated
  customer runtime description from the configuration docs.

### Added

- **Customer personas.** Personas are YAML files: a synthetic profile, an engine
  (`chained` or `realtime`), optional first message, language and voice, the
  expectations the agent under test must meet, deterministic checks, and a fidelity
  scenario. Three ship in the package; `PSTN_CUSTOMER_PERSONAS_DIR` adds private ones.
  `PSTN_CUSTOMER_PROFILES` now maps each line to a default persona id.
- **Persona queue.** `customer queue NUMBER --persona ID` (or `--file`, `--inline`)
  sets the persona for that line's next call, so a coding agent can change the
  customer per test. The call keeps a copy of the persona it ran with.
- **Grading against the persona.** `customer grade CALL_ID [--wait]` runs the
  persona's checks and one ElevenLabs judge test per expectation on the transcript,
  stores the verdicts and reasons on the call, and exits 2 when the call fails.
  The `/elevenlabs/post-call` webhook stores transcripts and grades automatically.
- **Persona checks.** `customer check-persona --persona ID [--repeat N]` runs an
  ElevenLabs simulation in which a simulated dealership calls the persona agent,
  and judges whether the customer stayed in character (exit 2 on any failed run).
- **Background noise and turn-taking.** Personas take `environment` (an ElevenLabs
  background preset and volume) and `turn` (eagerness, silence timeout). Because
  ElevenLabs sets these per agent, `sync-agent` creates a variant agent for each
  combination from the engine's base agent and keeps its voice in step.
- **Pinned persona agents.** `sync-agent` enables ElevenLabs versioning and keeps a
  `telephony-voice-simulator` branch on every persona agent; calls, checks and grading
  run on it, and each session and grade records the exact agent version.
- API routes for all of the above, and a persona catalog, queue and grade view on
  the console Inbox.

### Changed

- **The synthetic customer runs on ElevenLabs Agents.** The webhook registers each
  allowlisted call with one persona agent through ElevenLabs' register-call API,
  and the agent holds the conversation over the call's media stream, including
  interruptions. It replaces the Twilio speech-gather, OpenAI and text-to-speech
  turn loop, and the `/twilio/customer/turn` and `/twilio/customer/audio` routes.
  Set `ELEVENLABS_CUSTOMER_AGENT_ID`; `CUSTOMER_AGENT_MODEL` is no longer read.
- `customer sync-agent` pushes the persona prompt, mu-law audio, call limit, model
  and per-call override permissions to each persona agent (an optional second agent
  runs ElevenLabs Realtime), and `customer refresh` copies a call's transcript from
  ElevenLabs without the Realtime model's expression tags. The console Inbox does both.

## 0.2.0

Every simulator capability is now reachable three ways: the `telephony-voice-sim`
command, the HTTP API, and the web console. All three call one shared service layer.

### Breaking changes

This release is a clean break: there are no compatibility aliases.

- **One CLI with command groups.** `telephony-voice-sim` replaces the flat commands
  and every `python -m telephony_voice_simulator.*` entry point and backend script.

  | 0.1.x | 0.2.0 |
  |---|---|
  | `add-provider`, `providers` | `providers add`, `providers list` |
  | `add-endpoint`, `endpoints` | `lines add`, `lines list` (also `update`, `delete`) |
  | `add-extension`, `delete-extension`, `directory` | `directory add`, `directory delete`, `directory list` |
  | `validate` | `scenarios validate` |
  | `simulate`, `sim` | `scenarios simulate` (`--kind pbx` replaces `--pbx`) |
  | `run`, `runs`, `cancel-run`, `simulate-ivr` | `runs create`, `runs list`, `runs cancel`, `runs ivr` |
  | `calls` | `calls list` (also `show`, `delete`, `recording`) |
  | `dealer-map`, `dealer-call`, `dealer-case`, `dealer-suite`, `dealer-capacity` | `pbx map`, `pbx call`, `pbx cases` / `pbx case`, `pbx suite`, `pbx capacity` |
  | `python -m ...benchmark` | `benchmark plan`, `run`, `compare`, `rescore`, `preflight`, `reports`, `report` |
  | `python -m ...deploy_twilio`, `...grade_hosted` | `hosted deploy`, `queue`, `smoke`, `grade` |
  | `python -m ...agent_call` | `hosted agent-call` |
  | `python -m ...agent_api`, `...runner` | `agents trigger`, `call`, `record`, `room` |
  | `scripts/twilio_number.py list`, `attach`, `restore` | `numbers inventory`, `numbers point`, `numbers revert` |
  | `python -m ...corpus.*`, `scripts/validate_public_audio.py`, `scripts/generate_ivr_prompts.py` | `corpus build`, `import`, `from-recording`, `validate-public`, `prompts` |
  | `scripts/uat_matrix.py` | `uat configs`, `check`, `plan`, `run` |
  | `scripts/analysis/classify_silence.py` | `analyze silence FILE` |

- **Removed the `amd-sim` and `telephony-sim` command aliases.**
- **Removed `POST /api/simulations`.** Use `POST /api/ivr/simulations`.
- **Removed `PSTN_NUMBER_MAP`.** Route numbers with managed lines (fixed or queued).
  Numbers that no line manages answer with `PSTN_DEFAULT_SCENARIO`.
- **Removed the standalone Twilio webhook entry point.** Use
  `telephony-voice-sim serve --with-pstn`.
- **The Python package was reorganized.** It now has `api`, `cli`, `service`,
  `storage`, `domains`, `grading`, `telephony`, `integrations`, `evaluation` and
  `corpus` packages. Import paths from 0.1.x no longer resolve.
- **CLI errors exit with code 1.** Service errors print one line and exit 1.
  Configuration refusals that exited 2 now exit 1.

### Added

- **Console pages:**
  - Built-in models (IVR journeys, and PBX stories with extension overrides)
  - Dealer PBX (map, call routing with transfers, named cases and suite, capacity)
  - Benchmarks, UAT and Agents
  - Hosted callee and Account numbers
  - Corpus, Inbox (SMS, synthetic customer sessions) and Analysis
  - Operations
- **Confirmations:** before an action places real calls, spends paid APIs, deploys,
  or rewires a live Twilio number, the console asks and says what will happen.
- **Background operations:** long jobs (benchmark and UAT runs, deploys, smoke
  calls, grading, corpus builds, prompt rendering, agent calls, LiveKit rooms)
  answer `202` with an operation. You poll it for live log lines and the result.
  It is stored in SQLite or MySQL. A restart marks unfinished operations failed.
- **API routes for every capability:** scenario detail and validation, model
  simulation, the dealer PBX, single calls, hosted Twilio, account numbers,
  agents, benchmarks, UAT, corpus, SMS, synthetic customer and transcript analysis.
- **Path safety:** browser input names config files, number backups, benchmark
  reports and scenarios only by name. It never passes a server path.

### Fixed

- The CLI can queue runs on Twilio lines while a separate process serves the API.
- The LiveKit agent call appends to the hosted Twilio queue instead of replacing
  it, so it no longer drops other queued scenarios.
- The Twilio-only app refuses cross-origin writes, like the control API.
- A corpus import can no longer lose its manifest entry to a build running
  at the same time.
