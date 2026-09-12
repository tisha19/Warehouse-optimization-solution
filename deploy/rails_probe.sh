#!/usr/bin/env bash
# Ask Guardrails to judge the planner's own goal and print the raw verdict.
# Runs ON the compute node.
set -uo pipefail

PORT="${1:-28003}"
CONFIG_ID="${2:-warehouse}"

read -r -d '' BODY <<'JSON'
{
  "model": "nvidia/nvidia/nemotron-3.5-lightning",
  "messages": [
    {"role": "user", "content": "{\"goal\": \"Reduce picker travel over the next seven days. Stay within the move cap, keep cold-chain stock where it is, and prioritise the highest-demand lines.\", \"constraints\": {\"max_moves\": 30}}"}
  ],
  "guardrails": {"config_id": "CONFIG_ID_PLACEHOLDER"}
}
JSON
BODY="${BODY/CONFIG_ID_PLACEHOLDER/$CONFIG_ID}"

for i in 1 2 3; do
  echo "=== attempt $i ==="
  start=$(date +%s)
  out=$(curl -s -m 120 -w '\nHTTP %{http_code} in %{time_total}s' \
    -X POST "http://127.0.0.1:${PORT}/v1/guardrail/checks" \
    -H 'Content-Type: application/json' -d "$BODY")
  echo "$out" | head -c 900
  echo
  echo "(wall $(( $(date +%s) - start ))s)"
  echo
done

echo "=== configured rails ==="
curl -s -m 20 "http://127.0.0.1:${PORT}/v1/guardrail/configs/${CONFIG_ID}" | head -c 1200
echo
