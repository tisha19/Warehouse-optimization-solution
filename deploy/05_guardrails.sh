#!/usr/bin/env bash
# 05 - Start NeMo Guardrails as an HTTP policy service.
#
# The planner posts {"stage": ..., "payload": ...} to NEMO_GUARDRAILS_URL/validate
# and expects {"allowed": bool, "reason": str, "policy_id": str}.
# Guardrails is configured from ./guardrails_config.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
source ./_lib.sh

mkdir -p ./guardrails_config

start_container warehouse-guardrails \
  --network host \
  -e NVIDIA_API_KEY="${NIM_CLOUD_API_KEY}" \
  -e OPENAI_API_KEY="${NIM_CLOUD_API_KEY}" \
  -v "$(pwd)/guardrails_config:/config" \
  -e GUARDRAILS_PORT="${GUARDRAILS_PORT}" \
  "${GUARDRAILS_IMAGE}"

if ! wait_for_http "http://${SERVICE_HOST}:${GUARDRAILS_PORT}/health" 300 "NeMo Guardrails"; then
  echo
  echo "Guardrails did not come up. The planner treats this as optional and applies"
  echo "its local baseline policy instead. Leave NEMO_GUARDRAILS_URL empty in .env"
  echo "if you want to skip remote validation entirely."
fi

echo "Next: ./06_openshell.sh"
