# Architecture

## Merge decision

AMD, IVR, and PBX simulation belong in one project because they share the
same call boundary: endpoints, provider connections, routing, DTMF, timelines,
recordings, results, and operator workflows. They should remain separate
scenario packs because their state machines and grading rules differ.

The resulting dependency direction is:

```text
web-console
    |
control API / CLI
    |
catalog + provider registry + repository + normalized timeline
    |
AMD pack          IVR pack          PBX pack
    |
optional LiveKit and Twilio adapters
```

AMD is therefore a domain on top of the telephony simulation platform, not a
second platform.

## Two applications

- `apps/simulator-backend` is the source of truth. It owns scenario discovery,
  execution adapters, persistence, recordings, grading, and the HTTP API.
- `apps/web-console` is a replaceable client. Its static scenario reference is
  generated from the backend catalog, so scenario data is not maintained twice.

## Deliberately not copied

The old telephony project had a Next.js server layer, Drizzle schema,
PostgreSQL service, and Twilio Function app alongside its Python simulator.
Those duplicated responsibilities now owned by the Python control plane,
shared repository, provider adapters, and unified web console. The call behavior
was ported; the duplicate runtime and UI implementations were not.

## Portability boundary

Provider-specific code stays under
`src/telephony_voice_simulator/telephony/providers`. The common
`telephony` package imports those independent modules through a provider
registry; the application service never imports a carrier implementation.
Scenario packs expose provider-neutral
catalog entries and normalized timeline steps. A new carrier or voice-agent
integration should be an adapter, not a fork of a scenario pack.

Endpoint routing modes have executable control-plane semantics. A `fixed`
endpoint is pinned to its configured default scenario; a run request cannot
override that default. A `queued` endpoint accepts a scenario per run and uses
its configured default only as a fallback.

The local simulator executes AMD, IVR, and PBX. The Twilio runtime exposes two live
entry points from the same process:

- `/twilio/voice` executes queued AMD callee scenarios.
- `/twilio/ivr` gathers DTMF, resolves the shared extension directory,
  and bridges to its configured destination.

PBX remains a deterministic model rather than a SIP registrar or production
PBX. Adding live SIP/PBX execution is an adapter extension and does not require
another application.

## Module ownership

| Package | Responsibility |
|---|---|
| `control/` | HTTP/CLI orchestration, shared repository and normalized records |
| `telephony/` | Provider contracts, registry and carrier-specific runtime/management |
| `domains/` | Small deterministic IVR and PBX journey fixtures in the shared catalog |
| `pbxsim/` | Detailed multi-site PBX configuration, routing, capacity and transfer-policy model |
| `pstn/` | AMD compilation/grading, optional synthetic customer and SMS helpers, legacy entry points |
| `corpus/` | Audio generation/import, provenance and public-release policy |
| `components/console/` | Focused operator views and metrics; shared reads remain in `use-console-data.ts` |

The dealer-group PBX model has its own CLI suite. It does not execute live
consult/transfer media and is not yet a control-API scenario domain. Keep that
distinction visible when comparing its results with a carrier-backed test.

Synthetic customer sessions use the shared call repository for history and
deletion tombstones. Dialogue/audio generation remains process-local and bounded;
restart ends the conversation. Call deletion evicts both carrier and customer
session caches. Public artifacts exclude `*.local.*` scenario files; local
backend discovery still accepts them.

Twilio grading is coordinated by `telephony/providers/twilio/finalization.py`.
It saves pending work and the original grading specification in the existing call
analysis record, coalesces callbacks, waits for downloads, and resumes pending/error
jobs at startup. Carrier completion and grade completion remain separate facts.
Late media can revise a grade without adding another call-completed event.
The mechanism assumes one active Twilio runtime per repository and is not a
distributed worker queue.
