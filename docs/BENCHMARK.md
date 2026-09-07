# AMD benchmarking

A graded call answers "did the agent handle this callee correctly once?".
A benchmark answers the questions you ship on:

- how often is a **person** scored as a machine (the agent leaves a voicemail
  into a live conversation and hangs up on a reachable customer),
- how often is a **machine** scored as a person (the agent holds a
  conversation with a mailbox and burns the whole record window),
- how long detection takes, and
- which callee shapes fail **repeatedly** rather than once.

The suite drives real PSTN calls through the agent's own control plane, so a
run reflects the prompt, model, telephony and answering-machine configuration
that production would resolve — not a hand-built dispatch payload.

## How a benchmark call works

```text
benchmark ──queue scenario──▶ simulator line (Twilio Sync)
          ──POST trigger────▶ agent backend ──SIP──▶ simulator line
          ◀─poll call record─ agent backend
          ◀─recording────────  Twilio
          grade: agent-reported end reason + detection layer
               + sim-side audio (timing, overlap, message content)
```

Both halves matter. The sim side proves what actually happened on the wire
(did the message start after the beep, did the agent talk over the greeting,
was the message intact). The agent's own call record proves what the agent
*believed* happened (`ended_reason`, detection layer, AMD verdict and delay) —
the checks a sim-only run has to skip.

## Configure the agent adapter

`telephony_voice_simulator.agent_api` is vendor-neutral: it needs a URL that
places an outbound call, a JSON body template, and dotted paths to the fields
in the response and the call record.

```bash
SIM_NUMBER=+15550100000

AGENT_API_TRIGGER_URL=https://agent.example.test/calls/outbound/trigger
AGENT_API_AUTHORIZATION="Bearer <token>"
AGENT_API_BODY=/path/to/your/body.json
AGENT_API_CALL_ID_PATH=callId

AGENT_API_RECORD_URL=https://agent.example.test/internal/calls/{call_id}
AGENT_API_STATUS_PATH=status
AGENT_API_ENDED_REASON_PATH=providerMetadata.ended_reason
AGENT_API_DETECTION_LAYER_PATH=providerMetadata.voicemail_detection_layer
AGENT_API_AMD_PATH=providerMetadata.amd_result
```

`AGENT_API_BODY` points at a JSON file whose string values are scanned for
`{sim_number}`, `{scenario}` and `{run_id}`. Start from
`apps/simulator-backend/config/agent-api.body.example.json`. Keep the real
file and its credentials outside the repository.

`AGENT_API_DETECTION_LAYER_PATH` is optional. When a platform has no single
layer string, the adapter synthesizes `"<detector>-amd:<reason>"` from the AMD
object for a boolean machine verdict. A human verdict never creates a detection
layer. Detector delays must be finite, nonnegative numbers.

The trigger body and record must be JSON objects. The record URL must include
`{call_id}`; identifiers are URL-encoded before substitution. Record-only reads
need no trigger URL or body template. Polling checks immediately, retries
transport failures, HTTP 408/429 and server failures, and stops on permanent
errors such as expired credentials. A nonterminal snapshot is never returned
as a completed call.

Verify the wiring on one call before spending a suite:

```bash
python -m telephony_voice_simulator.agent_api call --scenario stock_voicemail_beep_1000
```

## Define a suite

```yaml
version: 1
name: amd-core
repeat: 3
pace_seconds: 20
call_timeout_seconds: 300
scenarios: [stock_voicemail_beep_1000, business_receptionist_human]
exclude: []
```

`scenarios: all` runs the whole catalog. `repeat` matters: answering-machine
detection is probabilistic, so a single pass proves the path works but does
not measure a rate. Use three or more attempts before quoting a number.

`pace_seconds` spaces calls within each lane. Each unique number gets one lane;
duplicate numbers are rejected, and `concurrency` is capped at the line count.
Use disjoint line pools for simultaneous suites. Repeat and concurrency must be
positive integers, and pace and call timeout must be finite (timeout positive).

`apps/simulator-backend/config/benchmark.example.yaml` ships a 13-scenario
shortlist: the smallest set that still covers both error directions and the
awkward middle (gates and screeners).

## Run

```bash
cd apps/simulator-backend
uv run python -m telephony_voice_simulator.benchmark --suite config/benchmark.example.yaml --dry-run
uv run python -m telephony_voice_simulator.benchmark --suite config/benchmark.example.yaml
```

Useful flags: `--scenario NAME` (repeatable) for an ad-hoc run, `--repeat N`,
`--number` (repeatable), `--concurrency`, `--pace`, `--no-audio` to skip collecting
simulator-side grading, and
`--out DIR` to place the report.

`report.json` and `report.md` are replaced atomically after every completed
attempt. Reports include `planned_calls` and `complete`, so a partial run is
visible. When an agent call cannot be confirmed terminal, its lane stops before
reusing that line. An interrupted in-flight attempt may not yet appear in the
report. The CLI exits nonzero for failed, incomplete, or indeterminate runs.

The optional `scripts/run_benchmark.sh PROFILE SUITE` wrapper validates the plan,
performs one preflight call, then starts the suite only if the pipeline worked.
Each preflight has a fresh report directory. Passing `--dry-run` to the wrapper
only prints the plan and places no calls. A failed agent assertion is valid
preflight evidence; a missing simulator leg or broken API is not.

## Read the report

Every scenario carries a callee class — `machine`, `human`, or `screener` —
derived from its own `expect` block, or stated outright as
`expect.callee_class` when the assertions are too unusual to classify.

`screener` is deliberately its own class. A call-screening assistant or DTMF
gate **is** a machine that the agent must detect and navigate, but the call
must still end as a live conversation. Scoring it as either plain machine or
plain human would hide the failure that matters. In the error rates, screeners
count as live callees: a screener read as a mailbox is the same production
failure as a person read as one.

The report gives a confusion matrix, headline error rates in both directions,
detection-delay percentiles, a per-scenario pass rate over the repeats, and
the failed check text for every failing attempt.

An `indeterminate` result is neither pass nor fail. If a required check cannot be
verified, the call remains indeterminate unless another check explicitly fails.
Overall and per-scenario pass rates both exclude indeterminate calls. Error
rates exclude unknown classifications; `classification_coverage` reports the
fraction of calls with a usable classification. Failed, busy, canceled and
unanswered calls remain unknown. A mailbox treated as either a human or a
navigable screener counts toward the false-human rate. The p90 delay uses the
nearest-rank percentile.

The benchmark also verifies that the simulator played the requested scenario.
Calls are matched by the dedicated destination line and start time, so unrelated
traffic must not share benchmark lines during a run.

Use `--rescore PATH/report.json` to recompute classifications and aggregate
metrics from existing evidence without redialing. This does not rerun audio
analysis or change stored assertion verdicts. Use
`--compare baseline=a/report.json candidate=b/report.json` for a side-by-side report.

## Cost and blast radius

Every attempt is a real outbound call and a real inbound leg. Cost and
wall-clock scale with `scenarios x repeat`. Run `--dry-run` first: it prints
the call count and the expected class for each scenario without dialing.

Point the suite at a test agent and a test line. A misconfigured body template
dials whatever number it contains.
