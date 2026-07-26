# Generic UAT matrix

`apps/simulator-backend/scripts/uat_matrix.py` replaces the former
integration-specific orchestration. It contains no tenant IDs, customer
payloads, storage locations, credentials, or assumptions about one agent
vendor.

The matrix file defines:

- Scenario names and optional per-case variables
- An ordered list of named workflow steps
- Per-step retry/poll behavior and failure handling
- Optional JSON response values captured for later steps
- A run-specific artifact directory
- Timeout, delay, and stop/continue behavior

Supported adapters are:

- `command`: executes an argument array without a shell
- `http`: makes a JSON HTTP request
- `noop`: useful when validating or documenting a matrix

Secrets use `{from_env: VARIABLE_NAME}` and are resolved only during execution.
Header values and adapter environments are omitted from reports.

## Validate and preview

```bash
cd apps/simulator-backend
uv run python scripts/uat_matrix.py --config config/uat.example.yaml --check
uv run python scripts/uat_matrix.py --config config/uat.example.yaml --dry-run
```

The included example invokes the generic LiveKit outbound adapter. That command
already performs trigger, polling, grading, and result persistence. For an
integration whose operations are separate, express the complete evidence flow
as ordered steps:

```yaml
version: 1
continue_on_failure: true
artifacts_dir: results/uat/artifacts
matrix:
  - scenario: stock_voicemail_beep_1000
    variables:
      test_line: "+15550101000"

steps:
  - name: queue
    adapter:
      type: http
      method: POST
      url: https://agent.example.test/v1/test-runs
      headers:
        Authorization: {from_env: UAT_AGENT_AUTHORIZATION}
      json:
        scenario: "{scenario}"
        client_run_id: "{run_id}"
        destination: "{test_line}"
    capture_json:
      remote_run_id: data.id

  - name: trigger
    adapter:
      type: http
      method: POST
      url: https://agent.example.test/v1/test-runs/{remote_run_id}/start
      headers:
        Authorization: {from_env: UAT_AGENT_AUTHORIZATION}

  - name: poll
    attempts: 30
    interval_seconds: 5
    adapter:
      type: command
      timeout_seconds: 30
      argv: [./integration/poll-run, "{remote_run_id}"]

  - name: grade
    adapter:
      type: command
      timeout_seconds: 120
      argv:
        - ./integration/grade-run
        - "{remote_run_id}"
        - "{scenario}"

  - name: collect_artifacts
    run_if: always
    adapter:
      type: command
      timeout_seconds: 120
      argv:
        - ./integration/fetch-artifacts
        - "{remote_run_id}"
        - "{artifact_dir}"
```

Each adapter attempt succeeds on a zero command exit or a 2xx HTTP response.
For polling, make the adapter return failure while the remote run is pending;
the runner retries it up to `attempts`, waiting `interval_seconds` between
attempts. A failed step skips later `run_if: success` steps. A
`run_if: always` artifact step still executes, which preserves logs and call
evidence for failed runs.

`capture_json` maps safe context variable names to dotted paths in a successful
adapter's JSON output. Captured values are available to later templates and
command environments, but the report records only the captured key names.
Command steps also receive `UAT_SCENARIO`, `UAT_RUN_ID`,
`UAT_ARTIFACT_DIR`, and any captured value as upper-case `UAT_*` variables.

Run reports are written to `results/uat/` and include every named step, attempt
counts, status, duration, and the generated run ID. Step artifact files belong
under `{artifact_dir}`. Reports and artifacts must not be committed. An adapter
should return non-zero or non-2xx when grading fails.

Adapter bodies, rendered arguments/URLs, stdout, and stderr are redacted from
the JSON report by default because they may contain customer data even when
credentials are environment-only. A step may set `report_output: true` when
its output is intentionally sanitized and needed as evidence. Treat that as an
explicit data-retention decision; the 12 KB bound limits size, not sensitivity.

The older `trigger` plus optional `verifier` syntax remains accepted and is
normalized to the same two-step workflow. Do not mix it with `steps`.

Start with a small matrix. Real PSTN runs incur provider costs and can reach
real people when a destination is misconfigured.
