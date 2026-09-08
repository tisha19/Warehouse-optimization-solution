#!/usr/bin/env bash
# Bring up the full self-hosted stack on the current GPU node.
# Idempotent: re-running replaces containers that are already there.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"

command -v module >/dev/null 2>&1 || { source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null; }
module load rootless-docker/1.75

KEY=$(grep -m1 -oE 'nvapi-[A-Za-z0-9_-]+' .env)
CACHE=/raid/docker/tmp/nim-cache-$USER
mkdir -p "$CACHE" && chmod 777 "$CACHE"
mkdir -p deploy/state deploy/openshell/state
CLOUD="https://integrate.api.nvidia.com/v1"

ULTRA_IMAGE=${ULTRA_IMAGE:-nvcr.io/nim/nvidia/nemotron-3-ultra-550b-a55b:2.0.12}
LIGHTNING_IMAGE=${LIGHTNING_IMAGE:-nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b:latest}
CUOPT_IMAGE=${CUOPT_IMAGE:-nvcr.io/nvidia/cuopt/cuopt:26.8.0-cu13}
GUARDRAILS_IMAGE=${GUARDRAILS_IMAGE:-nvcr.io/nvidia/nemo-microservices/guardrails:25.12}

# Both NIMs share the node, so neither may take vLLM's default ~90% of each GPU.
ULTRA_GPU_UTIL=${ULTRA_GPU_UTIL:-0.55}
# KV cache dominates the allocation and this NIM ignores the utilisation knob,
# so cap the context length instead to leave GPU memory for cuOpt.
ULTRA_MAX_MODEL_LEN=${ULTRA_MAX_MODEL_LEN:-16384}
LIGHTNING_GPU_UTIL=${LIGHTNING_GPU_UTIL:-0.12}

log(){ printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
wait_http(){ local u=$1 t=$2 n=$3 w=0
  printf '  waiting for %s ' "$n"
  while [ $w -lt "$t" ]; do
    curl -sf -o /dev/null "$u" && { printf ' READY (%ss)\n' "$w"; return 0; }
    sleep 10; w=$((w+10)); [ $((w % 120)) -eq 0 ] && printf '%sm' $((w/60)) || printf '.'
  done
  printf ' TIMEOUT after %ss\n' "$t"; return 1; }

cat > deploy/state/stack.env <<ENVEOF
STACK_NODE=$(hostname)
STACK_JOB_ID=${SLURM_JOB_ID:-interactive}
STACK_STARTED=$(date -Is)
ENVEOF
log "stack node: $(hostname)  job: ${SLURM_JOB_ID:-interactive}"

log "cuOpt solver"
docker rm -f warehouse-cuopt >/dev/null 2>&1
docker run -d --name warehouse-cuopt --gpus '"device=3"' --shm-size=8g -p 5000:5000 "$CUOPT_IMAGE" >/dev/null
wait_http http://127.0.0.1:5000/cuopt/health 300 "cuOpt"

log "cuOpt slotting adapter"
docker build -q -t warehouse-cuopt-adapter ./deploy/cuopt_adapter >/dev/null
docker rm -f warehouse-cuopt-adapter >/dev/null 2>&1
docker run -d --name warehouse-cuopt-adapter --network host \
  -e CUOPT_SERVER_URL=http://127.0.0.1:5000 -e ADAPTER_PORT=8002 warehouse-cuopt-adapter >/dev/null
wait_http http://127.0.0.1:8002/health 120 "adapter"

log "NeMo Guardrails"
docker rm -f warehouse-guardrails >/dev/null 2>&1
docker run -d --name warehouse-guardrails --network host \
  -e GUARDRAILS_PORT=8003 -e DEFAULT_LLM_PROVIDER=openai \
  -e NIM_ENDPOINT_URL="$CLOUD" -e NVIDIA_API_KEY="$KEY" \
  -e OPENAI_API_KEY="$KEY" -e OPENAI_BASE_URL="http://127.0.0.1:8010/v1" \
  "$GUARDRAILS_IMAGE" >/dev/null
wait_http http://127.0.0.1:8003/v1/health 300 "guardrails"
curl -s -X POST http://127.0.0.1:8003/v1/guardrail/configs -H 'Content-Type: application/json' \
  -d @deploy/guardrails_warehouse_config.json -o /dev/null -w '  warehouse rails config: %{http_code}\n' || true

log "OpenShell Governor"
pkill -f 'services.openshell.governor' >/dev/null 2>&1
OPENSHELL_GOVERNOR_PORT=8004 nohup ./.venv/bin/python -m services.openshell.governor > /tmp/governor.log 2>&1 &
wait_http http://127.0.0.1:8004/api/v1/healthz 120 "openshell governor"

# Ultra spans all four GPUs, so it must claim its memory before Lightning does.
log "Nemotron 3 Ultra supervisor NIM (tp4, util=${ULTRA_GPU_UTIL})"
docker rm -f warehouse-nim-supervisor >/dev/null 2>&1
docker run -d --name warehouse-nim-supervisor --gpus all --shm-size=64g \
  -e NGC_API_KEY="$KEY" -e NIM_TENSOR_PARALLEL_SIZE=4 \
  -e NIM_GPU_MEMORY_UTILIZATION="$ULTRA_GPU_UTIL" \
  -e NIM_MAX_MODEL_LEN="$ULTRA_MAX_MODEL_LEN" \
  -v "$CACHE:/opt/nim/.cache" -p 8000:8000 "$ULTRA_IMAGE" >/dev/null
wait_http http://127.0.0.1:8000/v1/health/ready 10800 "ultra NIM" || log "ultra still loading; continuing"

log "Nemotron 3.5 Lightning sub-agent NIM (util=${LIGHTNING_GPU_UTIL})"
docker rm -f warehouse-nim-subagent >/dev/null 2>&1
docker run -d --name warehouse-nim-subagent --gpus '"device=3"' --shm-size=16g \
  -e NGC_API_KEY="$KEY" -e NIM_GPU_MEMORY_UTILIZATION="$LIGHTNING_GPU_UTIL" \
  -v "$CACHE:/opt/nim/.cache" -p 8010:8000 "$LIGHTNING_IMAGE" >/dev/null
wait_http http://127.0.0.1:8010/v1/health/ready 3600 "lightning NIM" || log "lightning still loading; continuing"

log "planner UI"
pkill -f 'showcase_server.py' >/dev/null 2>&1
nohup ./.venv/bin/python showcase_server.py --host 0.0.0.0 --port ${APP_PORT:-8090} > /tmp/planner_ui.log 2>&1 &
wait_http http://127.0.0.1:${APP_PORT:-8090}/api/state 180 "planner UI"

log "stack up"
docker ps --format '  {{.Names}}\t{{.Status}}'
