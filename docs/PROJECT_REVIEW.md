# Project review: 2026-10-22

## Current assessment

Keep the shared Python backend and static Next.js console. The checked-in
catalog contains 33 AMD/callee scenarios, seven IVR journeys, and four PBX
journeys. The separate dealer-group model has 30 executable cases. This checkout
also contains a private local scenario, which remains usable by the backend.
Scenario count measures fixture coverage, not carrier or handset fidelity.

The architecture supports three different levels of evidence:

1. `mock` dispatch renders a scenario timeline without running an agent or audio.
2. The IVR/PBX and dealer-group engines execute deterministic routing models.
3. Twilio and LiveKit integrations exercise call/media behavior when connected
   to external test infrastructure. Local unit tests stub those integrations.

Do not interpret a completed dry run, a synthetic persona conversation, or a
provider completion callback as proof of a correct business outcome. The grader
and UAT assertions need the matching recordings, transcripts, and application
records.

## Scope inspected

| Layer | Source and findings |
|---|---|
| Control plane | `api/app.py`, `service.py`, `store.py`, `mysql_store.py`: shared CRUD, durable queue claims/cancellation, recording containment, privacy tombstones, SQLite/MySQL storage |
| Scenario authoring | `domains/catalog.py`, `domains/amd/validation.py`, `scenarios/`, `corpus/`: validated AMD vocabulary, provenance and release policy, generated static reference |
| AMD/media | `domains/amd/machine.py`, `integrations/livekit/callee.py`, `integrations/livekit/runner.py`, `telephony/twilio/compile.py`, `grading/analyze.py`, `grading/checks.py`: room playback, PSTN compilation, timing/content checks, explicit missing-evidence handling |
| Routing | `domains/ivr.py`, `domains/pbx/stories.py`, `domains/pbx/dealer/`: built-in journey fixtures plus a richer dealer-group router, channel capacity, schedules and transfer-policy evaluation |
| Providers | `telephony/providers/`: Twilio live runtime and management, mock dispatch, Telnyx configuration boundary with live execution explicitly unavailable |
| Agent evaluation | `integrations/agent_api.py`, `integrations/livekit/agent_call.py`, `evaluation/benchmark.py`, UAT scripts/config: external-agent dispatch, terminal polling, repetitions, classification scoring and business assertions |
| Customer/SMS | `telephony/twilio/customer.py`, `sms_receiver.py`: optional synthetic customer, bounded speech turns, signed allowlisted SMS inbox |
| Console | `console-app.tsx`, resource editors, histories, `use-console-data.ts`, API client: shared operator state, mutations, polling, filtering, recordings and exports |
| Distribution | Compose overlays, ingress configuration, Dockerfiles, packaging manifests, CI and public release checker: private control API boundary and artifact contents |

## Local changes reviewed and updated

The starting worktree changed only `customer_agent.py` and its tests. It added
outbound persona selection and an eight-second pause after the closing audio.
Both behaviors are retained. Additional regressions reproduced ten failures in
outbound metadata and conversation lifecycle behavior before the fixes.

- Select an outbound persona using `From`, but preserve the provider's actual
  `From`, `To`, and outbound direction in persisted history. Later analysis writes
  preserve the original direction.
- Stop generating after goodbye, generation failure, turn/time limits, provider
  completion, or deletion. Retries reuse the closing response while the call is
  active. Check provider state again after each awaited generation step.
- Integrate customer-session eviction with shared call deletion. Remove generated
  audio access, reject late entry/turn requests, and discard generation results
  when completion or deletion happens during a provider request.
- Expire audio access after ten minutes without depending on another call starting.
  Audio responses use `Cache-Control: no-store`. Require a configured voice ID
  before accepting a customer call.
- Explicitly close SMS SQLite connections after their commit or rollback.
- Extract overview metric rendering and calculations into `overview-metrics.tsx`.
  The existing layout, labels and API contract are preserved.
- Fix a reproduced distribution leak: the ignored local scenario was included in
  both the wheel and source archive and in generated static reference pages.
  Exclude `*.local.*` from package archives and Docker contexts, require an explicit
  local static-reference opt-in, and reject private files in archive release checks.
- Document customer/SMS configuration in the shared configuration reference and
  example environment file.
- Patch `follow-redirects` in the optional standalone IVR lockfile and correct
  that subproject's runtime requirement to Node 22, matching `twilio-run@5.0.1`.
  The main web console continues to use Node.js 26.12.1.

The original private scenario and existing runtime data were preserved. No
commits, pushes, deployments, number changes, or live calls were performed.

## Structure to keep

`control/` owns orchestration and persistence; `telephony/` owns carrier adapters;
`domains/` owns the small built-in routing journeys; `domains/pbx/dealer/` owns the detailed
multi-site model; `pstn/` owns compilation, grading and optional customer/SMS
helpers. Compatibility imports remain for existing callers.

Keep the two PBX levels explicit: the four catalog scenarios support basic
operator journeys, while the 30 dealer cases exercise detailed routing and
transfer policies through the CLI. They currently do not share a live PBX media
runtime. Moving their files together would not close that capability gap.

The console's shared fetch/mutation state stays in `use-console-data.ts`; reusable
views belong under `components/console/`. Further extraction from the large
console/controller or repository files should follow a behavior change and its
focused tests. A package move alone would add churn.

## Recording finalization follow-up

Extracted background grading into `telephony/twilio/finalization.py`.
The runtime retains the twelve-second callback grace, then awaits active recording
downloads. Each request has a fifteen-second timeout and there are six bounded
attempts. Duplicate callbacks share one download and one grading job.

Pending jobs and the original scenario/compiled grading specification are saved
in the existing call analysis field before work starts. Startup discovers pending
and error jobs without the history page limit, restores their snapshot and resumes
missing downloads. Recovery does not depend on the scenario remaining in the
current catalog. Shutdown cancels tracked tasks and leaves pending work available
for the next start. Unfinished full-call downloads remain discoverable even when
message grading has already completed. This is a single-runtime recovery mechanism, not a distributed
job queue with worker leases.

Unknown or failed message delivery produces an indeterminate media grade. An
explicit provider `absent` result remains observed evidence of no message. Full-call
audio is excluded from mailbox-message grading. A late recording refreshes the
call and linked run, preserving the original completion timestamp and timeline.
New media marks an earlier pass as pending while it is downloaded and analyzed.

Grading runs outside the webhook event loop. Results are discarded after deletion,
including deletion during a body download or analysis. The runtime rejects stale
nonterminal callbacks after completion, preserves provider duration, and fails
closed for malformed continuation indexes/sequences. The existing IVR distinction
between child dial outcome and parent call completion remains intact.

The touched Twilio HTTP clients use the current authorization-header helper,
available within the project's existing aiohttp requirement, following the
[aiohttp client reference](https://docs.aiohttp.org/en/stable/client_reference.html).

## Backend correctness follow-up: 2026-10-23

Focused on call execution and grading without further console changes:

- Cache the original AMD voice response and each execution step's continuation.
  Retried callbacks do not duplicate keypresses, replay runtime transitions, or
  consume prompt attempts. Reject mismatched and stale execution steps.
- Freeze the scenario and compiled program when answering, so later catalog
  mutations cannot change an active call's DTMF routing or grading expectations.
- Preserve authored recording windows by disabling silence timeout and finish
  keys in both local and hosted TwiML generation.
- Bind message callbacks to execution steps and grade in execution order.
  Reject recording reassignment and keep a missing first message distinct from
  a later message whose callback arrived first.
- Include audio between a requested marker and the recording boundary in timing
  grades. Keep unsupported timing/overlap checks visible as indeterminate.
  Ambiguous message order cannot select an arbitrary file for transcription.

Verification: **452 backend tests passed, 17 MySQL tests skipped** without a
dedicated test database. Python lint, hosted JavaScript syntax, and patch
whitespace checks passed. No live carrier calls or external transcription were
used. Callback idempotency changes apply to the local Python AMD runtime; the
separate hosted Sync runtime still needs equivalent retry/concurrency handling.

## Feature priorities

These are recommendations derived from current code and coverage, not features
implemented by this pass.

| Priority | Capability | Why it matters and acceptance evidence |
|---|---|---|
| 1 | Business-outcome customer cases | Reuse the UAT runner for booking, callback, opt-out and wrong-person cases. Match simulator SID, agent call ID, transcript and expected saved records. A case passes only when the expected business record exists and forbidden side effects are absent. Keep the synthetic customer free of booking tools. |
| 2 | Two-leg transfer media harness | Connect the existing dealer transfer-policy model to actual consult/bridge legs. Cover consult voicemail, busy/no-answer, rejected transfer, agent recovery and caller continuity, with per-leg recordings and channel accounting. |
| 3 | Reproducible impairment profiles | Add seeded codec/noise/dropout/delay profiles and one-way-audio/early-media fixtures. Save the seed and impairment parameters with each run. Separate signaling failures from an answered line playing a disconnected-number prompt. |
| 4 | Carrier/handset and language regression packs | Add reviewed captures and clean-room equivalents for the actual languages and devices used in testing. Save capture date, device/software version and provenance. Score human false positives, machine false negatives, screener navigation and timing separately. |
| 5 | Unified evaluation workflow | Surface existing benchmark, UAT and dealer-suite results through the control API and console, with scenario/config fingerprints and comparisons. Preserve current CLI commands and distinguish modeled outcomes from agent grades. |

Twilio documents separate call directions and asynchronous callbacks in the
[Call resource](https://www.twilio.com/docs/voice/api/call-resource). Its
[Gather documentation](https://www.twilio.com/docs/voice/twiml/gather) describes
speech collection and nested playback, and its
[Dial documentation](https://www.twilio.com/docs/voice/twiml/dial) describes
bridged call behavior. Those are integration boundaries to test against; the
roadmap ordering above is an engineering judgment based on this repository.

## Verification for this pass

The backend suite passed **439 tests**, with **17 MySQL integration tests skipped**
because no dedicated test database was configured. The 30 dealer cases and all
built-in IVR/PBX CLI journeys also passed. Focused regressions cover the customer
lifecycle, outbound history, SQLite closure, private archive members, static
reference export, delayed/duplicate media callbacks, stale grades, shutdown/restart,
scenario changes, bounded failures, deletion races and malformed continuations.

Python and JavaScript lint, TypeScript, semantic-theme validation, UAT/prompt
configuration, license checks, the production static build, wheel/source builds
and public-release checks passed. The public build exports 49 pages for 44
scenarios. Real rebuilt wheel and source archives contain zero `*.local.*`
members, compared with one each before the fix.

The web production dependency audit found no known advisories. The optional
standalone `twilio-run` development dependency tree retains nine advisories
(seven high, two moderate), with no critical advisories. Its latest published
version remains 5.0.1; npm's proposed forced remediation downgrades the CLI to
2.1.1. That incompatible change was not applied. These packages are local
deployment tooling and are not bundled into the deployed IVR Function. Review
the upstream dependencies before using that optional CLI with account access.

Browser verification used a fresh temporary SQLite database, a loopback API and
the production static console. The run showed `Checks incomplete` before delivery,
then `Checks passed` after an actual local recording callback/download. The call
history loaded and decoded the synthetic one-second WAV; the media endpoint returned
HTTP 200, 8,000 samples at 8 kHz. The run retained two timeline events and its original
completion timestamp. No browser console errors were observed. The extracted
overview metrics matched the test API's resource counts.

No real MySQL service, Docker image build, live carrier call, external generation/
transcription request, or LiveKit media session was verified in this pass. Live
latency and final conversational behavior still require controlled carrier tests.

The earlier review follows as historical context.

---

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
