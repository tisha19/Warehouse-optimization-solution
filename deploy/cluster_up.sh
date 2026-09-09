#!/usr/bin/env bash
# Bring up the full self-hosted stack on the current GPU node.
# Idempotent: re-running replaces containers that are already there.
#
# Default topology keeps the NIM and cuOpt on different GPUs so they cannot
# contend for memory: Nemotron 3.5 Lightning on GPU 0, cuOpt on GPU 3.
# Set NIM_PROFILE=ultra to serve Nemotron 3 Ultra instead; it needs tp4 and
# therefore spans every GPU, which starves cuOpt on a 4-GPU allocation.
#
# Re-running only starts what is not already answering its health check, so it
# doubles as a resume after a partial bring-up. FORCE=1 recreates everything.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"

command -v module >/dev/null 2>&1 || { source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null; }
module load rootless-docker/1.75

KEY=$(grep -m1 -oE 'nvapi-[A-Za-z0-9_-]+' .env)
CACHE=/raid/docker/tmp/nim-cache-$USER
mkdir -p "$CACHE" && chmod 777 "$CACHE"
mkdir -p deploy/state deploy/openshell/state

NIM_PROFILE=${NIM_PROFILE:-lightning}
LIGHTNING_IMAGE=${LIGHTNING_IMAGE:-nvcr.io/nim/nvidia/nemotron-3.5-lightning-30b-a3b:latest}
ULTRA_IMAGE=${ULTRA_IMAGE:-nvcr.io/nim/nvidia/nemotron-3-ultra-550b-a55b:2.0.12}
CUOPT_IMAGE=${CUOPT_IMAGE:-nvcr.io/nvidia/cuopt/cuopt:26.8.0-cu13}
GUARDRAILS_IMAGE=${GUARDRAILS_IMAGE:-nvcr.io/nvidia/nemo-microservices/guardrails:25.12}

if [ "$NIM_PROFILE" = "ultra" ]; then
  NIM_IMAGE="$ULTRA_IMAGE"; NIM_GPUS='"device=0,1,2,3"'; NIM_TP=4
  NIM_MODEL_ID=${NIM_MODEL_ID:-nvidia/nemotron-3-ultra-550b-a55b}
else
  NIM_IMAGE="$LIGHTNING_IMAGE"; NIM_GPUS='"device=0"'; NIM_TP=1
  NIM_MODEL_ID=${NIM_MODEL_ID:-nvidia/nemotron-3.5-lightning-30b-a3b}
fi
NIM_PORT=${NIM_PORT:-8000}
CUOPT_GPUS=${CUOPT_GPUS:-'"device=3"'}

log(){ printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
healthy(){ curl -sf -m 5 -o /dev/null "$1"; }
# Returns 0 when the caller should skip the service because it is already serving.
# FORCE=1 rebuilds everything from scratch.
skip_if_up(){ [ "${FORCE:-0}" = 1 ] && return 1
  healthy "$1" && { log "$2 already up - skipping"; return 0; }; return 1; }
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
STACK_NIM_PROFILE=${NIM_PROFILE}
STACK_NIM_MODEL=${NIM_MODEL_ID}
STACK_NIM_PORT=${NIM_PORT}
ENVEOF
log "node $(hostname) | job ${SLURM_JOB_ID:-interactive} | nim profile ${NIM_PROFILE} (${NIM_MODEL_ID})"

log "cuOpt solver"
if ! skip_if_up http://127.0.0.1:5000/cuopt/health "cuOpt"; then
  docker rm -f warehouse-cuopt >/dev/null 2>&1
  eval docker run -d --name warehouse-cuopt --gpus "$CUOPT_GPUS" --shm-size=8g -p 5000:5000 "$CUOPT_IMAGE" >/dev/null
  wait_http http://127.0.0.1:5000/cuopt/health 300 "cuOpt"
fi

log "cuOpt slotting adapter"
if ! skip_if_up http://127.0.0.1:8002/health "adapter"; then
  docker build -q -t warehouse-cuopt-adapter ./deploy/cuopt_adapter >/dev/null
  docker rm -f warehouse-cuopt-adapter >/dev/null 2>&1
  docker run -d --name warehouse-cuopt-adapter --network host \
    -e CUOPT_SERVER_URL=http://127.0.0.1:5000 -e ADAPTER_PORT=8002 warehouse-cuopt-adapter >/dev/null
  wait_http http://127.0.0.1:8002/health 120 "adapter"
fi

log "OpenShell Governor"
if ! skip_if_up http://127.0.0.1:8004/api/v1/healthz "openshell governor"; then
  pkill -f 'services\.openshell\.governor' >/dev/null 2>&1
  OPENSHELL_GOVERNOR_PORT=8004 nohup ./.venv/bin/python -m services.openshell.governor > /tmp/governor.log 2>&1 &
  wait_http http://127.0.0.1:8004/api/v1/healthz 120 "openshell governor"
fi

log "${NIM_PROFILE} NIM on port ${NIM_PORT}"
if ! skip_if_up "http://127.0.0.1:${NIM_PORT}/v1/health/ready" "${NIM_PROFILE} NIM"; then
  docker rm -f warehouse-nim >/dev/null 2>&1
  docker rm -f warehouse-nim-supervisor warehouse-nim-subagent >/dev/null 2>&1
  eval docker run -d --name warehouse-nim --gpus "$NIM_GPUS" --shm-size=32g \
    -e NGC_API_KEY="$KEY" -e NIM_TENSOR_PARALLEL_SIZE=$NIM_TP \
    -v "$CACHE:/opt/nim/.cache" -p ${NIM_PORT}:8000 "$NIM_IMAGE" >/dev/null
  wait_http "http://127.0.0.1:${NIM_PORT}/v1/health/ready" 5400 "${NIM_PROFILE} NIM" || log "NIM still loading; continuing"
fi

# A NIM advertises its own model id, which does not always match the NGC image
# name (Lightning serves "nvidia/nemotron-3.5-lightning"). Take it from the
# server so the app and the rails cannot drift out of sync and 404.
SERVED_MODEL=$(curl -sf -m 10 "http://127.0.0.1:${NIM_PORT}/v1/models" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null) || SERVED_MODEL=""
[ -n "$SERVED_MODEL" ] || SERVED_MODEL="$NIM_MODEL_ID"
log "NIM serves ${SERVED_MODEL}"
echo "STACK_NIM_SERVED_MODEL=${SERVED_MODEL}" >> deploy/state/stack.env
python3 - "$SERVED_MODEL" <<'PYEOF'
import pathlib, re, sys
served = sys.argv[1]
path = pathlib.Path(".env")
text = path.read_text()
for key in ("NIM_MODEL", "NIM_SUBAGENT_MODEL"):
    text = re.sub(rf"(?m)^{key}=.*$", f"{key}={served}", text)
path.write_text(text)
PYEOF

# Guardrails rails run on the local NIM, so it must be up before the config lands.
log "NeMo Guardrails"
if ! skip_if_up http://127.0.0.1:8003/v1/health "guardrails"; then
  docker rm -f warehouse-guardrails >/dev/null 2>&1
  docker run -d --name warehouse-guardrails --network host \
    -e GUARDRAILS_PORT=8003 -e DEFAULT_LLM_PROVIDER=openai \
    -e NIM_ENDPOINT_URL="http://127.0.0.1:${NIM_PORT}/v1" -e NVIDIA_API_KEY="$KEY" \
    -e OPENAI_API_KEY="local" -e OPENAI_BASE_URL="http://127.0.0.1:${NIM_PORT}/v1" \
    "$GUARDRAILS_IMAGE" >/dev/null
  wait_http http://127.0.0.1:8003/v1/health 300 "guardrails"
fi
# The rails config always gets re-applied so it tracks the model actually served.
python3 - "$SERVED_MODEL" <<'PYEOF'
import json, pathlib, sys
p = pathlib.Path("deploy/guardrails_warehouse_config.json")
c = json.loads(p.read_text()); c["data"]["models"][0]["model"] = sys.argv[1]
p.write_text(json.dumps(c, indent=2))
PYEOF
curl -s -X POST http://127.0.0.1:8003/v1/guardrail/configs -H 'Content-Type: application/json' \
  -d @deploy/guardrails_warehouse_config.json -o /dev/null -w '  rails config: %{http_code}\n'
curl -s -X PATCH http://127.0.0.1:8003/v1/guardrail/configs/default/warehouse -H 'Content-Type: application/json' \
  -d @deploy/guardrails_warehouse_config.json -o /dev/null -w '  rails patch : %{http_code}\n'

log "planner UI"
if ! skip_if_up "http://127.0.0.1:${APP_PORT:-8090}/api/dashboard" "planner UI"; then
  pkill -f 'showcase_server\.py' >/dev/null 2>&1
  nohup ./.venv/bin/python showcase_server.py --host 0.0.0.0 --port "${APP_PORT:-8090}" > /tmp/planner_ui.log 2>&1 &
  wait_http "http://127.0.0.1:${APP_PORT:-8090}/api/dashboard" 180 "planner UI"
fi

log "stack up"
docker ps --format '  {{.Names}}\t{{.Status}}'
