#!/usr/bin/env bash
# 03 - Start the compact sub-agent NIM used by the demand/inventory/warehouse specialists.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
source ./_lib.sh

start_container warehouse-nim-subagent \
  --gpus "\"device=${SUBAGENT_GPUS}\"" \
  --shm-size=16g \
  -e NGC_API_KEY="${NGC_API_KEY}" \
  -v "${NIM_CACHE_DIR}:/opt/nim/.cache" \
  -p "${NIM_SUBAGENT_PORT}:8000" \
  "${NIM_SUBAGENT_IMAGE}"

wait_for_http "http://${SERVICE_HOST}:${NIM_SUBAGENT_PORT}/v1/models" 1800 "sub-agent NIM"

echo "Advertised models:"
curl -s "http://${SERVICE_HOST}:${NIM_SUBAGENT_PORT}/v1/models" | head -c 800; echo
echo "Next: ./04_cuopt.sh"
