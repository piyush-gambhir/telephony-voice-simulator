# End-to-end migration audit

Last reviewed: 2026-07-26.

## Result

The two former repositories now have one source of truth:

```text
web-console
    ↓
control API / CLI
    ↓
provider registry + SQLite/MySQL repository + normalized calls/runs
    ↓
AMD pack       IVR pack       PBX pack
    ↓
mock          LiveKit        Twilio/PSTN
```

The original repositories remain untouched as migration references. The new
repository does not depend on either one at runtime.

`PROD_AMD_ANALYSIS.md` and `SILENCE_TIMEOUT_ANALYSIS.md` were intentionally
retained outside the public repository. They contain production-derived
counts, rates, distributions, and failure-mining conclusions, not executable
runtime behavior. The generalized classifier and scenario regressions were
migrated; private operational measurements remain a separate data asset.

## Capability parity

| Former capability | Merged implementation | Status |
|---|---|---|
| AMD YAML state machines | `scenarios/`, `machine.py` | Migrated |
| AMD timing/content assertions | `assertions.py` | Migrated and extended |
| Room-mode LiveKit callee | `bot.py`, `runner.py`, `webhook_sink.py` | Migrated |
| Deployed-agent outbound dispatch | `agent_call.py`, `dispatch.py` | Migrated |
| Local Twilio AMD webhook runtime | `telephony/providers/twilio/webhooks.py` (`/twilio/voice`) | Migrated |
| Twilio-hosted AMD machine | `pstn/deploy_twilio.py`, `pstn/twilio_hosted/` | Migrated |
| DTMF relay and call grading | `pstn/compile.py`, `pstn/analyze.py`, `pstn/grade_hosted.py` | Migrated |
| Corpus generation and capture import | `corpus/build_corpus.py`, `corpus/import_asset.py`, `corpus/from_recordings.py` | Migrated |
| Production-miss classifier | `scripts/analysis/classify_silence.py` | Generalized |
| Fixed tenant UAT script | `scripts/uat_matrix.py` | Replaced with vendor-neutral pipeline |
| AMD scenario website | Generated `/docs` routes in `web-console` | Migrated |
| Telephony IVR model | `domains/ivr.py` | Migrated: 7 behaviorally distinct canonical IDs plus aliases |
| Telephony PBX model | `domains/pbx.py` | Migrated: departments, extensions, roles, devices, legs, CRM, CDR |
| Provider profiles | Independent `telephony/providers` modules plus common registry | Migrated |
| Extension directory | Shared store/service/API/console/live IVR | Migrated |
| Persisted call runs | Additive SQLite schema plus optional MySQL repository | Migrated |
| Twilio Functions IVR | `deploy/twilio-ivr` | Migrated and hardened |
| IVR prompt generation | `scripts/generate_ivr_prompts.py` | Migrated with neutral prompt source |
| Provider/extension/simulator web UI | `/console` | Rebuilt over the shared API |
| Old PostgreSQL demo seed | Removed | No demo-data mutation is exposed in the product |

The scenario catalog is 32 AMD + 7 IVR + 4 PBX = 43. Current unified IDs remain
aliases where the older telephony CLI exposed different canonical IDs.

## Intentional replacements

The following code was not copied because its responsibility already exists
in the merged backend:

- The telephony web application's server actions, Drizzle schema, migrations,
  and PostgreSQL service were replaced by the Python service and shared
  SQLite/MySQL repository boundary.
- Its separate UI shell and simulator API route were replaced by the static
  console calling the same backend used by the CLI and live IVR.
- Its two Twilio Function handlers were consolidated into one protected,
  bounded-retry handler. A standalone deployment remains available when an
  operator does not want the shared repository directory.
- The fixed-tenant UAT payload, tenant IDs, customer identity, storage bucket,
  and vendor-specific backend calls were replaced by configurable command/HTTP
  steps with environment-only secrets.
- The private Asterisk prompt/beep fixture was replaced by independently
  authored prompt fragments and a synthesized tone. The replacement retains
  both the message-start window and the no-speech-over-tone regression check.

## Dead-code and redundancy audit

- Removed the superseded `pstn/provision.py`; `scripts/twilio_number.py` is the
  single surgical attach/backup/restore workflow.
- Removed the generic screener and one-step iPhone screener fixtures in favor
  of the fuller Pixel, assistant, and iOS state-transition scenarios.
- Removed the second IVR connect-happy-path fixture; extension 1502 remains
  exercised by retry and no-answer behavior, while extension 1501 represents
  the parameterized successful bridge path.
- Generated scenario JSON, web audio, Next output, compiled PSTN audio,
  databases, recordings, caches, and dependency folders are ignored rather
  than treated as source.
- The web console contains no orphaned UI modules; each retained component is
  imported by an application surface.
- `runner.py`, `agent_call.py`, `deploy_twilio.py`, `grade_hosted.py`, and the
  corpus tools are executable operational entry points, not unused library
  code. They are retained because they cover room, deployed-agent, hosted-PSTN,
  and corpus workflows that the control API does not duplicate.
- Provider-specific behavior stays behind adapters. The catalog, storage,
  directory, run history, and timeline model are shared once.
- The inherited endpoint `routing_mode` is no longer dormant configuration:
  `fixed` pins dispatch to the endpoint default, while `queued` permits a
  per-run scenario override.

## Data compatibility

SQLite migrations are additive and preserve databases created by earlier
iterations of the merged repository. The old telephony PostgreSQL database is
not opened directly by the new runtime. Its provider, extension, and run
fields all have equivalents in the new model; export operational records,
review them for customer data, and import only approved data rather than
automatically copying an unknown production database.

New deployments can instead set a MySQL URI. This is a storage choice, not a
return to the removed duplicate Node/PostgreSQL control plane.

## Runtime and security checks

- Operator APIs, call history, and recording media stay on loopback or private
  authenticated ingress. Public carrier ingress never forwards `/api/*`.
- Twilio webhook signatures remain required; unsigned mode is an explicit
  local-only override.
- Recording defaults to off. Media reads and deletion are confined to the
  exact simulator-owned call directory and reject symlink/path escapes.
- Local call deletion removes database rows, simulator-owned artifacts, and
  active memory state, then tombstones the SID so late callbacks cannot
  recreate it. Provider-hosted `RecordingSid` copies require separate Twilio
  retention or deletion.
- Public fixtures use example identities and reserved 555 numbers; the former
  operational IVR number was removed.

## Validation contract

A migration is considered complete only when all of these pass:

```bash
pnpm lint
pnpm test
pnpm build
pnpm uat:check
pnpm prompts:check
docker compose config --quiet
```

The final verification also starts fresh API and static-console processes,
creates workspace records through the control API, exercises AMD and IVR
callbacks plus IVR/PBX model runs, and
fetches the exported console, scenario pages, and CSS over HTTP. A visual
browser review remains a release checklist item because automated browser
access may be unavailable in restricted local environments.
