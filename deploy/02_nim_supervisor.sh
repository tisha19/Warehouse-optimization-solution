#!/usr/bin/env bash
# 02 - Start the supervisor NIM (Nemotron 3 Ultra) used by the planner node.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
source ./_lib.sh

start_container warehouse-nim-supervisor \
  --gpus "\"device=${SUPERVISOR_GPUS}\"" \
  --shm-size=32g \
  -e NGC_API_KEY="${NGC_API_KEY}" \
  -v "${NIM_CACHE_DIR}:/opt/nim/.cache" \
  -p "${NIM_SUPERVISOR_PORT}:8000" \
  "${NIM_SUPERVISOR_IMAGE}"

# Large MoE weights load slowly on a cold cache; allow a long first start.
wait_for_http "http://${SERVICE_HOST}:${NIM_SUPERVISOR_PORT}/v1/models" 3600 "supervisor NIM"

echo "Advertised models:"
curl -s "http://${SERVICE_HOST}:${NIM_SUPERVISOR_PORT}/v1/models" | head -c 800; echo
echo "If the id above differs from NIM_MODEL (${NIM_MODEL}), update deploy/config.env."
echo "Next: ./03_nim_subagent.sh"
