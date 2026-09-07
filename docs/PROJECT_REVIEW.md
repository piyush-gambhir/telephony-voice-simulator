# Project review — 2026-09-07

## Assessment

The repository already had a useful shared Python control plane, static Next.js
console, AMD audio engine, deterministic IVR/PBX models, and live Twilio/LiveKit
integration code. The initial backend suite passed 231 tests. Existing local
benchmark and dealer-group PBX work was preserved and completed within its
documented simulator scope.

The main gaps were disconnected operator workflows, unreliable handling of
missing grading evidence, insufficient validation at runtime boundaries,
resource lifecycle bugs, and unfinished benchmark orchestration. A new framework
or another control plane would have added maintenance without fixing those gaps.

## Implemented changes

| Area | Problem | Result |
| --- | --- | --- |
| Console | Provider, endpoint, directory, overview, and IVR tools existed but navigation hid them | All operational views are reachable; fixed endpoints show their actual configured scenario |
| Console data | A single resource failure discarded the initial load; history became stale | Independent resource recovery, visible-tab refresh, manual refresh, and protection against stale reads overwriting mutations |
| History | Runs/calls were difficult to find or reuse | Search, status/provider filters, pagination, JSON export, recording retry/download, and queued-run cancellation |
| Queue control | No safe operator cancellation | Atomic cancellation only while a run is unclaimed; cancellation and incoming-call claims are tested concurrently |
| Persistence | SQLite transactions did not close their connections; in-memory databases disappeared between operations | Connections close on commit and rollback; in-memory repositories retain isolated state |
| Validation | Invalid profiles could persist; API types were silently coerced | Validation before writes, consistent conflict errors, strict request fields, and validated ring timeouts |
| Scenario authoring | Validation existed mainly in duplicated test logic | Shared runtime validation, validation CLI/CI command, duplicate-name detection, and external scenario directories |
| Extensibility | Provider support was partially hardcoded in clients | Injectable provider registry and catalog-driven compatibility; documented adapter and console extension points |
| Playback | Audio was marked complete when queued; DTMF could wait through a greeting | Playout-aware timing, prompt cancellation, early DTMF handling, and one recording/timeline clock |
| Grading | Missing recordings, unknown checks, incomplete transcripts, duplicate tracks, and repeated prompts could distort results | Explicit indeterminate grades, schema-checked expectations, reliable speech segmentation, merged tracks, and repeated/interrupted playback intervals |
| IVR | Simulated durations disagreed with terminal events | Timeline-derived durations and ring-timeout behavior |
| Benchmarks | Nonterminal records, stale preflight files, duplicate lanes, and unknown required checks could be treated as success | Terminal polling, explicit failure reporting, safer lane orchestration, consistent denominators, and call-free dry runs |
| Dealer PBX | Stale lookup caches and gaps in capacity, schedules, and transfer policies | Reusable group validation, overnight schedules, capacity-aware routing, and transfer-policy enforcement |

## Cleanup decisions

Removed duplicate scenario schema checks, arbitrary minimum catalog counts,
initial-release-only assertions that prevented reviewed asset additions,
implementation module-name assertions, duplicate catalog reads, and SQL error
message matching. Extracted console state, history, status rendering, and
scenario compatibility into focused modules.

Compatibility imports and legacy CLI aliases remain because they preserve
documented integrations. Tests for recording containment, webhook signatures,
asset provenance, queue races, and migration behavior remain because they protect
observable behavior. An increased regression count is not itself the quality
target; the new cases reproduce concrete defects and exercise failure paths.

## Verification and boundaries

The backend tests use temporary databases, synthetic audio, stubbed agent and
carrier responses, and local HTTP servers. Browser verification uses a fresh
temporary database and the mock provider. No live calls, number provisioning,
deployment, production-data migration, or transcription requests were performed.

Final backend verification passed **391 tests**, including 17 real MySQL
integration cases added during the [follow-up test pass](TEST_REPORT.md). Python/JavaScript lint,
scenario validation, UAT and prompt configuration, license policy, the static
console build, Python wheel/source builds, and public audio release checks also
passed. The console build exports 48 pages.

The [additional test pass](TEST_REPORT.md) verifies real MySQL persistence and
concurrency, installed-wheel startup/restart, and the production console's
mobile, export, and recording workflows. It also records fixes exposed by
those tests. Live Twilio and LiveKit behavior still needs credentialed integration
testing. Telnyx remains an explicitly marked preview adapter. The PBX models
simulate routing; they are not a production SIP registrar. Room-mode message
content checks now report indeterminate because isolated mailbox transcription
is available through PSTN grading. The unauthenticated control API still belongs
on a private network, as described in [Deployment](DEPLOYMENT.md).

Use [Extending](EXTENDING.md) for the supported extension points and
[Benchmark](BENCHMARK.md) for offline validation and agent evaluation workflows.
