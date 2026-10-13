#!/usr/bin/env bash
# M18-09 / #341 experiment 4: integration call styles on the real model.
# See README.md. Hits the GPU: takes /tmp/homeai-stack.lock itself if the
# caller hasn't. Changes nothing on the stack: no users, no integrations.
#
# Usage:
#   scripts/eval/integrations/run.sh
#   EVAL_ONLY=over-range EVAL_REPEATS=1 scripts/eval/integrations/run.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
export COMPOSE_PROJECT_NAME=homeai

echo "==> capturing the agent's first request (fake model, this tree's agent-server)"
(cd services/agent-server && uv run --frozen python "$SCRIPT_DIR/capture_prefix.py" "$SCRIPT_DIR/prefix.json")

NETWORK="$(docker compose config --format json | python3 -c "
import json, sys
print(json.load(sys.stdin)['networks']['homeai-internal']['name'])
")"
MODEL_NAME="$(docker compose exec -T agent-server printenv MODEL_NAME)"
export MODEL_NAME EVAL_REPEATS="${EVAL_REPEATS:-3}" EVAL_ONLY="${EVAL_ONLY:-}" \
  EVAL_STYLES="${EVAL_STYLES:-fixed,native}"

echo "==> running against model-runner ($MODEL_NAME), ${EVAL_REPEATS} per case and style"
# The gateway image has jsonschema, which the eval validates arguments with
# exactly as the gateway does; homeai-internal has no internet to install it.
docker run --rm --label homeai.test=eval-integrations --network "$NETWORK" \
  -e MODEL_NAME -e EVAL_REPEATS -e EVAL_ONLY -e EVAL_STYLES \
  -v "$SCRIPT_DIR:/eval:ro" --entrypoint /app/.venv/bin/python homeai-integrations-gateway \
  /eval/run.py >"$SCRIPT_DIR/last-report.json"

python3 - "$SCRIPT_DIR/last-report.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
for style, s in report["summary"].items():
    print(f"{style:7} {s['passed']}/{s['runs']} passed, {s['invalid_arguments']} invalid_arguments "
          f"({s['recovered_after_invalid']} recovered), {s['model_calls']} model calls, {s['seconds']} s")
if report.get("probe"):
    for style, p in report["probe"].items():
        print(f"probe {style:7} next request: {p['next_processed']} of {p['next_prompt_tokens']} "
              f"prompt tokens processed, {p['next_prompt_ms']} ms")
PY
echo "==> report: $SCRIPT_DIR/last-report.json"
