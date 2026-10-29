# Regression suite

`telephony-voice-sim regression run` places one real call per case against a deployed
agent and reports pass/fail per case with the agent's call id. Run it before and after
any agent change (an SDK upgrade, a prompt, a model): a case that passed before and fails
now is a regression. Code: `apps/simulator-backend/src/telephony_voice_simulator/evaluation/regression.py`.

```bash
cd apps/simulator-backend
uv run telephony-voice-sim regression plan --suite config/<suite>.local.yaml
uv run telephony-voice-sim regression run --suite config/<suite>.local.yaml --markdown \
  [--case NAME ...] [--baseline results/regression/<report>.json] [--tolerance 0.3]
```

The API runs the same thing in the background: `POST /api/regression/runs`
`{"suite": "<file in config/>", "cases": [...], "baseline": "<report id>"}` answers 202
with an operation; `GET /api/regression/reports/{id}` returns the report and its table.

## Case kinds

| Kind | Call | Graded by |
|---|---|---|
| `amd` | The agent calls a scripted line (`line`) playing `scenario` | The scenario's own `expect` block and audio checks, as in the AMD benchmark |
| `outbound` | The agent calls the persona line (trigger body `body`) | The agent's record (`expect`) and the persona's grade |
| `inbound` | The persona line calls `agent_number`, keying `send_digits` first for an IVR | Same as outbound; `find_command` maps the call to the agent's call id |

A persona case with a `target` also queues a transfer-target scenario on the line the
agent transfers to; the target line's checks and the persona's code words then prove the
bridge (see [Transfers](TRANSFERS.md)). `before`/`after` commands set up one case, for
example the agent's transfer type, and `after` always runs. An `amd` case's
`scenario_expect` merges into the scenario's expectations, and a null drops one, for
checks that depend on the agent, such as `message_content: null` when the agent's
voicemail text differs from the scenario's.

## Assertions

`expect` reads the agent's own call record: `status`, `ended_reason`, `fine_reason`,
`transfer_status`, `tools_include`/`tools_exclude`, `transcript_contains`/`transcript_excludes`,
`latency_p50_ms_max`/`latency_p95_ms_max` (end of the user's turn to agent audio, tool
time included), `llm_p95_ms_max`, `tts_p95_ms_max` and `min_duration_s`. `persona: skip`
and `target: skip` drop those grades. A missing latency is indeterminate, never a pass.

The record comes from `AGENT_API_RECORD_URL`, or from `AGENT_API_RECORD_COMMAND`, a
command containing `{call_id}` that prints the record as JSON (nothing while it does not
exist yet), for agents whose records live in a warehouse rather than behind an API.

## Baseline

Every run writes `results/regression/<report id>.json`. Pass an earlier one as
`--baseline`: each case that passed there must still pass, and its e2e and LLM p95 may
grow at most `--tolerance` (default 30%) plus 250 ms. Keep the report of a run you
accepted as the baseline for the next change.
