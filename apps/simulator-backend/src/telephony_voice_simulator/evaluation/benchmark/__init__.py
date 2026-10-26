"""AMD benchmark suite: score a live agent against the scenario catalog.

A single graded call answers "did the agent handle this callee correctly?".
A benchmark answers the questions you actually ship on: how often does the
agent call a human a machine, how long does it take to decide, and which
callee shapes does it fail repeatedly rather than once.

The suite drives real PSTN calls. For every (scenario, attempt) pair it

  1. queues the scenario on the simulator line (its control API),
  2. asks the agent's own backend to place the outbound call
     (``agent_api``: real prompt, real model, real AMD configuration),
  3. waits for the agent's call record to reach a terminal status,
  4. grades the agent-reported end reason and detection layer against the
     scenario's ``expect`` block (the checks a sim-only run has to skip), and
  5. collects the simulator's own grading of the leg it answered.

The simulator grades every call against the compiled scenario it actually
played, so step 5 reads that analysis rather than re-deriving one: it already
holds the true timeline, the real DTMF presses, and the downloaded mailbox
recording.

It then aggregates a machine-vs-human confusion matrix, per-scenario pass
rates over repeats, and detection-latency percentiles.

Suite file (YAML):

    version: 1
    name: amd-core
    repeat: 3
    pace_seconds: 20
    call_timeout_seconds: 300
    numbers: ["+15550100000", "+15550100001"]
    scenarios: [stock_voicemail_beep_1000, business_receptionist_human]
    # or: scenarios: all
    # exclude: [number_disconnected_sit]

Each line is one concurrent lane. The scenario queue is per-number, so a lane
owns its line for the whole call and two lanes never race for the same queue;
n lines run n calls at once.

Modules: ``plan`` (suite and overrides into a plan), ``execution`` (live calls
and the lane pool), ``scoring`` (classes, checks, aggregation), ``reports``
(markdown, comparison, report files). The CLI is ``telephony-voice-sim
benchmark``; the entry points are in ``service.benchmarks``.
"""
