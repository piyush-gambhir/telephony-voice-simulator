#!/usr/bin/env bash
# Preflight one call, then run a full benchmark suite.
#
#   AGENT_API_AUTHORIZATION="Bearer <token>" \
#     scripts/run_benchmark.sh config/<profile>.local.env config/<suite>.local.yaml
#
# The agent token is read from the environment and is never written to disk:
# a value exported by the caller overrides whatever the profile file says, so
# a profile can ship with the field blank. Everything else — endpoints, body
# template, line pool — comes from the profile and the suite.
#
# The preflight places ONE real call before committing to the whole suite. A
# suite is dozens of carrier calls; an expired token or a bad mapping id
# should cost one of them, not all of them.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <profile.env> <suite.yaml> [--skip-preflight] [extra benchmark args...]" >&2
  exit 64
fi

profile="$1"
suite="$2"
shift 2

skip_preflight=0
if [[ "${1:-}" == "--skip-preflight" ]]; then
  skip_preflight=1
  shift
fi

cd "$(dirname "$0")/.."

for required in "$profile" "$suite"; do
  [[ -f "$required" ]] || { echo "missing: $required" >&2; exit 66; }
done

# Preserve a caller-supplied token across the profile sourcing below.
token="${AGENT_API_AUTHORIZATION:-}"

set -a
# shellcheck disable=SC1090
[[ ! -f .env ]] || source .env
# shellcheck disable=SC1090
source "$profile"
set +a

if [[ -n "$token" ]]; then
  export AGENT_API_AUTHORIZATION="$token"
fi

python="${PYTHON:-.venv/bin/python}"
[[ -x "$python" ]] || python="python3"

echo "profile : $profile"
echo "suite   : $suite"
echo "sim api : ${SIMULATOR_API_URL:-http://127.0.0.1:8978}"
# A wrapper dry run must never reach the paid preflight call.
for argument in "$@"; do
  if [[ "$argument" == "--dry-run" ]]; then
    exec "$python" -m telephony_voice_simulator.benchmark --suite "$suite" "$@"
  fi
done
"$python" -m telephony_voice_simulator.benchmark --suite "$suite" "$@" --dry-run

preflight_numbers=(--concurrency 1)
collect_number=0
for argument in "$@"; do
  if [[ "$collect_number" -eq 1 ]]; then
    preflight_numbers+=(--number "$argument")
    collect_number=0
  elif [[ "$argument" == "--number" ]]; then
    collect_number=1
  elif [[ "$argument" == --number=* ]]; then
    preflight_numbers+=("$argument")
  fi
done

if [[ "$skip_preflight" -eq 0 ]]; then
  echo
  echo "=== preflight: one call ==="
  mkdir -p results/benchmark
  preflight_dir="$(mktemp -d results/benchmark/preflight.XXXXXX)"
  # The call's own exit code reflects whether the AGENT passed the scenario,
  # which is not what a preflight is asking. Gate on pipeline health instead:
  # a genuinely failing agent is the result we came for, 93 times over.
  "$python" -m telephony_voice_simulator.benchmark \
    --suite "$suite" --scenario stock_voicemail_beep_1000 --repeat 1 \
    "${preflight_numbers[@]}" \
    --out "$preflight_dir" || true
  if ! "$python" -m telephony_voice_simulator.benchmark \
      --preflight-check "$preflight_dir/report.json"; then
    echo "the pipeline is broken — not starting the suite" >&2
    exit 1
  fi
fi

echo
echo "=== full suite ==="
exec "$python" -m telephony_voice_simulator.benchmark --suite "$suite" "$@"
