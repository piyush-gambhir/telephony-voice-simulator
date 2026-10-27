# Changelog

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
