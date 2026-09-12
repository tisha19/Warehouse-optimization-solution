#!/usr/bin/env bash
# One entry point for the whole platform. Run it from the login node.
#
#   ./deploy/runall.sh
#
# What it does, in order:
#   1. checks NVIDIA's hosted endpoint, which serves both Nemotron models
#   2. reports which local services are already up
#   3. submits the stack job if it is not running
#   4. follows the boot log until every service answers its health check
#   5. prints the final status and how to reach the UI
#
# No arguments needed. Safe to re-run: an already-healthy stack is left alone
# rather than restarted. Extra arguments are passed through to sbatch, so
# `./deploy/runall.sh --nodelist=dgx02` pins the node.
#
# Related:
#   deploy/forwardallports.sh  - run on YOUR laptop to reach these services
#   deploy/stack_status.sh     - status only, never starts anything
#   deploy/stack_down.sh       - stop the stack and release the GPU
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
export PATH="/cm/local/apps/slurm/current/bin:$PATH"
export SLURM_CONF="${SLURM_CONF:-/cm/shared/apps/slurm/etc/slurm/slurm.conf}"

ME="$(id -un)"
STACK_JOB="warehouse-stack"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-1800}"
SSH_HOST="${SSH_HOST:-ssh.axisapps.io}"
STATE_FILE="deploy/state/stack.$ME.env"

# Defaults must match cluster_up.sh; the state file overrides them once the
# stack has started and recorded where it actually landed.
CUOPT_PORT=25000; ADAPTER_PORT=28002; GUARDRAILS_PORT=28003
OPENSHELL_PORT=28004; APP_PORT=28090
if [ -f "$STATE_FILE" ]; then
  . "$STATE_FILE"
  CUOPT_PORT=${STACK_CUOPT_PORT:-$CUOPT_PORT}
  ADAPTER_PORT=${STACK_ADAPTER_PORT:-$ADAPTER_PORT}
  GUARDRAILS_PORT=${STACK_GUARDRAILS_PORT:-$GUARDRAILS_PORT}
  OPENSHELL_PORT=${STACK_OPENSHELL_PORT:-$OPENSHELL_PORT}
  APP_PORT=${STACK_APP_PORT:-$APP_PORT}
fi

bold(){ printf '\033[1m%s\033[0m\n' "$*"; }

env_get(){ grep -m1 -E "^$1=" .env 2>/dev/null | cut -d= -f2- ; }
job_id(){ squeue -h -u "$ME" -n "$1" -t RUNNING,PENDING -o '%i' 2>/dev/null | head -1; }
job_state(){ squeue -h -j "$1" -o '%T' 2>/dev/null | head -1; }
job_node(){ squeue -h -j "$1" -o '%N' 2>/dev/null | head -1; }

probe(){
  local label="$1" host="$2" port="$3" path="$4" code
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://${host}:${port}${path}" 2>/dev/null)
  if [ "$code" = "200" ]; then
    printf '  %-24s %-30s UP (200)\n' "$label" "$path"; return 0
  fi
  printf '  %-24s %-30s down (%s)\n' "$label" "$path" "${code:-000}"; return 1
}

# Both Nemotron models are hosted, so this is a credential and reachability
# check rather than something we could start ourselves.
hosted_probe(){
  local base key code
  base=$(env_get NIM_SUPERVISOR_BASE_URL); key=$(env_get NIM_SUPERVISOR_API_KEY)
  if [ -z "$base" ] || [ -z "$key" ]; then
    echo "  NIM_SUPERVISOR_BASE_URL / _API_KEY missing from .env"; return 1
  fi
  code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $key" "${base%/}/models" 2>/dev/null)
  if [ "$code" = "200" ]; then
    printf '  %-24s %-30s UP (200)\n' "hosted Nemotron" "${base%/}/models"; return 0
  fi
  printf '  %-24s %-30s down (%s)\n' "hosted Nemotron" "${base%/}/models" "${code:-000}"; return 1
}

status_report(){
  local stack_id stack_node down=0
  stack_id=$(job_id "$STACK_JOB")
  stack_node=$([ -n "$stack_id" ] && job_node "$stack_id")

  bold "== slurm =="
  squeue -u "$ME" -o '%.8i %.16j %.9T %.10M %.14R' 2>/dev/null | sed 's/^/  /'
  [ -z "$stack_id" ] && echo "  (no stack job)"

  bold ""
  bold "== models (NVIDIA hosted) =="
  echo "  orchestrator : $(env_get NIM_SUPERVISOR_MODEL)"
  echo "  specialists  : $(env_get NIM_SUBAGENT_MODEL)"
  hosted_probe || down=1

  bold ""
  bold "== application stack (job ${stack_id:-none} on ${stack_node:-?}) =="
  if [ -n "$stack_node" ]; then
    probe "cuOpt"              "$stack_node" "$CUOPT_PORT"      /cuopt/health   || down=1
    probe "cuOpt adapter"      "$stack_node" "$ADAPTER_PORT"    /health         || down=1
    probe "NeMo Guardrails"    "$stack_node" "$GUARDRAILS_PORT" /v1/health      || down=1
    probe "OpenShell governor" "$stack_node" "$OPENSHELL_PORT"  /api/v1/healthz || down=1
    probe "WarehouseIQ UI"     "$stack_node" "$APP_PORT"        /api/dashboard  || down=1
  else
    echo "  not running"; down=1
  fi
  return $down
}

# Only the job id goes to stdout; everything human-readable goes to stderr so
# the caller can capture the id with $(...).
submit(){
  local out
  bold "" >&2
  bold "== starting ${STACK_JOB} ==" >&2
  out=$(sbatch "$@" deploy/slurm_stack.sbatch 2>&1)
  echo "$out" | sed 's/^/  /' >&2
  case "$out" in
    *"Submitted batch job"*) echo "$out" | grep -oE '[0-9]+$' ;;
    *) echo "  -> submission refused; not waiting on it." >&2; return 1 ;;
  esac
}

# Follows the job log until the health check passes, so a slow start looks like
# progress rather than a hang.
wait_ready(){
  local jobid="$1" waited=0 node state log code
  bold ""
  bold "== waiting for the stack (job ${jobid}) =="
  while [ "$waited" -lt "$BOOT_TIMEOUT" ]; do
    state=$(job_state "$jobid")
    [ -z "$state" ] && { echo "  job ${jobid} left the queue - check warehouse-stack-${jobid}.log"; return 1; }
    node=$(job_node "$jobid")
    if [ -n "$node" ] && [ "$state" = "RUNNING" ]; then
      code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://${node}:${APP_PORT}/api/dashboard" 2>/dev/null)
      [ "$code" = "200" ] && { echo "  stack ready on ${node} after ${waited}s"; return 0; }
      log=$(ls -1t warehouse-stack-*.log 2>/dev/null | head -1)
      # Skip docker's per-layer pull spam; show progress lines only.
      [ -n "$log" ] && tail -3 "$log" 2>/dev/null \
        | grep -vE 'Pull complete|Download complete|Verifying Checksum|Downloading|Extracting|Waiting|Pulling fs layer' \
        | sed 's/^/    /'
      printf '  [%4ss] UI health=%s on %s\n' "$waited" "${code:-000}" "$node"
    else
      printf '  [%4ss] job is %s\n' "$waited" "$state"
    fi
    sleep 20; waited=$((waited + 20))
  done
  echo "  timed out after ${BOOT_TIMEOUT}s"
  return 1
}

bold "WarehouseIQ - initial status"
status_report
healthy=$?

if [ "$healthy" -eq 0 ]; then
  bold ""
  bold "Everything is already running; nothing to start."
else
  if [ -z "$(job_id "$STACK_JOB")" ]; then
    started=$(submit "$@") && wait_ready "$started"
  fi
  bold ""
  bold "WarehouseIQ - final status"
  status_report
  healthy=$?
fi

stack_node=$(job_node "$(job_id "$STACK_JOB")")
bold ""
if [ "$healthy" -eq 0 ] && [ -n "$stack_node" ]; then
  bold "All services are up on ${stack_node}."
  echo
  echo "  From your laptop:"
  echo "    ./deploy/forwardallports.sh <your-access-key>"
  echo "    then open http://127.0.0.1:${APP_PORT}"
  echo
  echo "  Or forward just the UI:"
  echo "    ssh -N -L ${APP_PORT}:${stack_node}:${APP_PORT} ${SSH_HOST} -l <your-access-key>"
  exit 0
fi

bold "Some services are still down - see the status above and the job logs."
exit 1
