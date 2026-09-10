#!/usr/bin/env bash
# 04 - Start the cuOpt solver plus the slotting adapter the planner talks to.
#
# The application posts a warehouse slotting problem to CUOPT_URL/solve/slotting.
# cuOpt does not expose that contract natively, so the adapter translates the
# problem into a linear assignment model, sends it to cuOpt, and maps the
# solution back into planner moves.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
source ./_lib.sh

start_container warehouse-cuopt \
  --gpus "\"device=${CUOPT_GPUS}\"" \
  --shm-size=8g \
  -p "${CUOPT_SERVER_PORT}:5000" \
  "${CUOPT_IMAGE}"

wait_for_http "http://${SERVICE_HOST}:${CUOPT_SERVER_PORT}/cuopt/health" 600 "cuOpt server" || \
  echo "  Health path differs between cuOpt releases; check 'docker logs warehouse-cuopt'."

echo "Building the slotting adapter image"
docker build -t warehouse-cuopt-adapter ./cuopt_adapter

start_container warehouse-cuopt-adapter \
  --network host \
  -e CUOPT_SERVER_URL="http://${SERVICE_HOST}:${CUOPT_SERVER_PORT}" \
  -e CUOPT_SOLVE_PATH="${CUOPT_SOLVE_PATH:-/cuopt/request}" \
  -e CUOPT_RESULT_PATH="${CUOPT_RESULT_PATH:-/cuopt/solution}" \
  -e ADAPTER_PORT="${CUOPT_ADAPTER_PORT}" \
  warehouse-cuopt-adapter

wait_for_http "http://${SERVICE_HOST}:${CUOPT_ADAPTER_PORT}/health" 120 "cuOpt slotting adapter"

echo "Next: ./05_guardrails.sh"
