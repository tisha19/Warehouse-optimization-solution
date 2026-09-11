#!/usr/bin/env bash
# Bring up the warehouse stack on the current GPU node.
# Idempotent: re-running replaces containers that are already there.
#
# Both Nemotron models run on NVIDIA's hosted endpoint: Ultra orchestrates and
# Lightning runs the specialists. We used to serve Lightning ourselves, but the
# NIM's vLLM build does not recognise the B300's native FP4 support, falls back
# to Marlin kernels and the engine core dies after loading the weights. Hosting
# is also the neighbourly choice here: cuOpt is now the only thing that needs a
# GPU, so the job asks for one instead of four.
#
# Ports default to a private 28xxx block. Several teammates share a node and
# run this same stack, so binding the obvious ports either fails outright or,
# worse, makes skip_if_up mistake their service for ours and silently wire the
# app to someone else's containers.
#
# Re-running only starts what is not already answering its health check, so it
# doubles as a resume after a partial bring-up. FORCE=1 recreates everything.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"

# The cluster module init scripts reference unset variables, so -u is lifted
# around them; a batch job otherwise dies here with no output at all.
set +u
command -v module >/dev/null 2>&1 || { source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null; }
module load rootless-docker/1.75 2>/dev/null || true
set -u
# A batch job does not always get the module environment, and the daemon does
# not survive the job that started it, so both are made explicit here.
export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
USER=${USER:-$(id -un)}
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
if ! docker info >/dev/null 2>&1; then
  echo "[boot] starting rootless docker on $(hostname)"
  mkdir -p "$XDG_RUNTIME_DIR"
  rm -rf "$XDG_RUNTIME_DIR/dockerd-rootless"
  if command -v start_rootless_docker >/dev/null 2>&1; then
    start_rootless_docker
  else
    nohup dockerd-rootless.sh --experimental \
      --data-root="/raid/docker/tmp/docker-container-storage-$(id -u)" \
      --storage-driver overlay2 > "/tmp/${USER:-$(id -un)}-dockerd-rootless.log" 2>&1 &
  fi
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
fi
docker info >/dev/null 2>&1 || { echo "rootless docker did not start; see /tmp/${USER:-$(id -un)}-dockerd-rootless.log" >&2; exit 1; }

# /tmp is shared with everyone else on the node, so a fixed log name belongs to
# whichever teammate started first and every later writer is denied.
LOGS="/tmp/${USER:-$(id -un)}-warehouse"
mkdir -p "$LOGS"

mkdir -p deploy/state deploy/openshell/state

env_get(){ grep -m1 -E "^$1=" .env | cut -d= -f2- ; }
HOSTED_BASE=$(env_get NIM_SUPERVISOR_BASE_URL)
HOSTED_KEY=$(env_get NIM_SUPERVISOR_API_KEY)
SUPERVISOR_MODEL=$(env_get NIM_SUPERVISOR_MODEL)
SPECIALIST_MODEL=${SPECIALIST_MODEL:-nvidia/nvidia/nemotron-3.5-lightning}
if [ -z "$HOSTED_BASE" ] || [ -z "$HOSTED_KEY" ] || [ -z "$SUPERVISOR_MODEL" ]; then
  echo "NIM_SUPERVISOR_BASE_URL / _API_KEY / _MODEL must be set in .env" >&2; exit 1
fi

CUOPT_IMAGE=${CUOPT_IMAGE:-nvcr.io/nvidia/cuopt/cuopt:26.8.0-cu13}
GUARDRAILS_IMAGE=${GUARDRAILS_IMAGE:-nvcr.io/nvidia/nemo-microservices/guardrails:25.12}

# GPUs are addressed by UUID rather than by index. The node ships a stale CDI
# spec that bind-mounts /run/nvidia-persistenced/socket, which no longer
# exists, so both --gpus and --device nvidia.com/gpu=N fail to create the
# container; --runtime=nvidia with NVIDIA_VISIBLE_DEVICES takes the legacy path
# that tolerates the missing socket. UUIDs also remove any doubt about whether
# an index refers to our allocation or to a GPU held by another job.
mapfile -t GPU_UUIDS < <(nvidia-smi --query-gpu=uuid --format=csv,noheader 2>/dev/null)
[ "${#GPU_UUIDS[@]}" -ge 1 ] || { echo "no GPUs visible to this job" >&2; exit 1; }
CUOPT_GPUS=${CUOPT_GPUS:-${GPU_UUIDS[0]}}

CUOPT_PORT=${CUOPT_PORT:-25000}
ADAPTER_PORT=${ADAPTER_PORT:-28002}
GUARDRAILS_PORT=${GUARDRAILS_PORT:-28003}
OPENSHELL_PORT=${OPENSHELL_PORT:-28004}
APP_PORT=${APP_PORT:-28090}
echo "[gpu]   cuopt=${CUOPT_GPUS} (${#GPU_UUIDS[@]} visible)"
echo "[model] supervisor=${SUPERVISOR_MODEL} specialists=${SPECIALIST_MODEL} @ ${HOSTED_BASE}"
echo "[port]  cuopt=${CUOPT_PORT} adapter=${ADAPTER_PORT} rails=${GUARDRAILS_PORT} openshell=${OPENSHELL_PORT} ui=${APP_PORT}"

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

# Per-user: the checkout is on shared storage and teammates run this too, so a
# single stack.env would describe whoever started last, not us.
STATE_FILE="deploy/state/stack.$USER.env"
cat > "$STATE_FILE" <<ENVEOF
STACK_NODE=$(hostname)
STACK_JOB_ID=${SLURM_JOB_ID:-interactive}
STACK_STARTED=$(date -Is)
STACK_SUPERVISOR_MODEL=${SUPERVISOR_MODEL}
STACK_SPECIALIST_MODEL=${SPECIALIST_MODEL}
STACK_CUOPT_PORT=${CUOPT_PORT}
STACK_ADAPTER_PORT=${ADAPTER_PORT}
STACK_GUARDRAILS_PORT=${GUARDRAILS_PORT}
STACK_OPENSHELL_PORT=${OPENSHELL_PORT}
STACK_APP_PORT=${APP_PORT}
ENVEOF
log "node $(hostname) | job ${SLURM_JOB_ID:-interactive} | models hosted by NVIDIA"

log "cuOpt solver"
if ! skip_if_up "http://127.0.0.1:${CUOPT_PORT}/cuopt/health" "cuOpt"; then
  docker rm -f warehouse-cuopt >/dev/null 2>&1
  docker run -d --name warehouse-cuopt --runtime=nvidia \
    -e NVIDIA_VISIBLE_DEVICES="$CUOPT_GPUS" -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    --shm-size=8g -p ${CUOPT_PORT}:5000 "$CUOPT_IMAGE" >/dev/null
  wait_http "http://127.0.0.1:${CUOPT_PORT}/cuopt/health" 300 "cuOpt"
fi

log "cuOpt slotting adapter"
if ! skip_if_up "http://127.0.0.1:${ADAPTER_PORT}/health" "adapter"; then
  docker build -q -t warehouse-cuopt-adapter ./deploy/cuopt_adapter >/dev/null
  docker rm -f warehouse-cuopt-adapter >/dev/null 2>&1
  docker run -d --name warehouse-cuopt-adapter --network host \
    -e CUOPT_SERVER_URL="http://127.0.0.1:${CUOPT_PORT}" -e ADAPTER_PORT=${ADAPTER_PORT} \
    warehouse-cuopt-adapter >/dev/null
  wait_http "http://127.0.0.1:${ADAPTER_PORT}/health" 120 "adapter"
fi

log "OpenShell Governor"
if ! skip_if_up "http://127.0.0.1:${OPENSHELL_PORT}/api/v1/healthz" "openshell governor"; then
  pkill -f 'services\.openshell\.governor' >/dev/null 2>&1
  OPENSHELL_GOVERNOR_PORT=${OPENSHELL_PORT} nohup ./.venv/bin/python -m services.openshell.governor > "$LOGS/governor.log" 2>&1 &
  wait_http "http://127.0.0.1:${OPENSHELL_PORT}/api/v1/healthz" 120 "openshell governor"
fi

# The repo sits on team-shared storage and a teammate running this same script
# rewrites .env for their own ports. Handing the URLs to our processes as real
# environment variables, with process precedence, stops that from repointing
# our app at their services.
export WAREHOUSE_ENV_PRECEDENCE=process
export CUOPT_URL="http://127.0.0.1:${ADAPTER_PORT}"
export NEMO_GUARDRAILS_URL="http://127.0.0.1:${GUARDRAILS_PORT}"
export OPENSHELL_URL="http://127.0.0.1:${OPENSHELL_PORT}"
export NIM_BASE_URL="$HOSTED_BASE"
export NIM_MODEL="$SPECIALIST_MODEL"
export NIM_API_KEY="$HOSTED_KEY"
export NIM_SUBAGENT_BASE_URL="$HOSTED_BASE"
export NIM_SUBAGENT_MODEL="$SPECIALIST_MODEL"

# The ports are chosen here, so this script is also what teaches the app where
# everything landed; otherwise a shared-node port change silently leaves .env
# pointing at a teammate's services.
python3 - "$HOSTED_BASE" "$SPECIALIST_MODEL" "$HOSTED_KEY" "$ADAPTER_PORT" "$GUARDRAILS_PORT" "$OPENSHELL_PORT" <<'PYEOF'
import pathlib, re, sys
base, specialist, key, adapter, rails, shell = sys.argv[1:7]
updates = {
    "NIM_BASE_URL": base,
    "NIM_MODEL": specialist,
    "NIM_API_KEY": key,
    "NIM_SUBAGENT_BASE_URL": base,
    "NIM_SUBAGENT_MODEL": specialist,
    "CUOPT_URL": f"http://127.0.0.1:{adapter}",
    "NEMO_GUARDRAILS_URL": f"http://127.0.0.1:{rails}",
    "OPENSHELL_URL": f"http://127.0.0.1:{shell}",
}
path = pathlib.Path(".env")
text = path.read_text()
for name, value in updates.items():
    line = f"{name}={value}"
    if re.search(rf"(?m)^{name}=", text):
        text = re.sub(rf"(?m)^{name}=.*$", line, text)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
path.write_text(text)
PYEOF

# The rails call a model themselves, so they point at the same hosted endpoint.
log "NeMo Guardrails"
if ! skip_if_up "http://127.0.0.1:${GUARDRAILS_PORT}/v1/health" "guardrails"; then
  docker rm -f warehouse-guardrails >/dev/null 2>&1
  docker run -d --name warehouse-guardrails --network host \
    -e GUARDRAILS_PORT=${GUARDRAILS_PORT} -e DEFAULT_LLM_PROVIDER=openai \
    -e NIM_ENDPOINT_URL="$HOSTED_BASE" -e NVIDIA_API_KEY="$HOSTED_KEY" \
    -e OPENAI_API_KEY="$HOSTED_KEY" -e OPENAI_BASE_URL="$HOSTED_BASE" \
    "$GUARDRAILS_IMAGE" >/dev/null
  wait_http "http://127.0.0.1:${GUARDRAILS_PORT}/v1/health" 300 "guardrails"
fi
# The rails config always gets re-applied so it tracks the model actually used.
python3 - "$SPECIALIST_MODEL" <<'PYEOF'
import json, pathlib, sys
p = pathlib.Path("deploy/guardrails_warehouse_config.json")
c = json.loads(p.read_text()); c["data"]["models"][0]["model"] = sys.argv[1]
p.write_text(json.dumps(c, indent=2))
PYEOF
curl -s -X POST "http://127.0.0.1:${GUARDRAILS_PORT}/v1/guardrail/configs" -H 'Content-Type: application/json' \
  -d @deploy/guardrails_warehouse_config.json -o /dev/null -w '  rails config: %{http_code}\n'
curl -s -X PATCH "http://127.0.0.1:${GUARDRAILS_PORT}/v1/guardrail/configs/default/warehouse" -H 'Content-Type: application/json' \
  -d @deploy/guardrails_warehouse_config.json -o /dev/null -w '  rails patch : %{http_code}\n'

log "UI bundle"
export PATH="$HOME/opt/node/bin:$PATH"
if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found on PATH (expected \$HOME/opt/node/bin) - cannot build the UI" >&2
  exit 1
fi
# Comparing against the mtime of web/src only catches edits to the directory
# itself, so a file copied into web/src/pages left a stale bundle in place.
if [ -f web/dist/index.html ] && [ -z "$(find web/src web/package.json -newer web/dist/index.html -print -quit 2>/dev/null)" ]; then
  echo "  bundle is current, skipping rebuild"
else
  ( cd web && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build ) || exit 1
fi

log "planner UI"
if ! skip_if_up "http://127.0.0.1:${APP_PORT}/api/dashboard" "planner UI"; then
  pkill -f 'showcase_server\.py' >/dev/null 2>&1
  nohup ./.venv/bin/python showcase_server.py --host 0.0.0.0 --port "${APP_PORT}" > "$LOGS/planner_ui.log" 2>&1 &
  wait_http "http://127.0.0.1:${APP_PORT}/api/dashboard" 180 "planner UI"
fi

log "stack up"
docker ps --format '  {{.Names}}\t{{.Status}}'
