#!/usr/bin/env bash
# One entry point for the whole platform. Run it from the login node.
#
#   ./deploy/runall.sh
#
# Reports what is already up, submits whatever is missing, follows the boot
# logs until each piece answers its health check, then prints the final status
# and the URL plus the port-forward command to reach it.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
export PATH="/cm/local/apps/slurm/current/bin:$PATH"
export SLURM_CONF="${SLURM_CONF:-/cm/shared/apps/slurm/etc/slurm/slurm.conf}"

ME="$(id -un)"
APP_PORT="${APP_PORT:-8090}"
STACK_JOB="warehouse-stack"
ULTRA_JOB="ultra-serve"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-3600}"   # Ultra cold-starts in ~24 min, warm in ~10.
SSH_HOST="${SSH_HOST:-ssh.axisapps.io}"

bold(){ printf '\033[1m%s\033[0m\n' "$*"; }
dim(){ printf '\033[2m%s\033[0m\n' "$*"; }

job_id(){ squeue -h -u "$ME" -n "$1" -t RUNNING,PENDING -o '%i' 2>/dev/null | head -1; }
job_state(){ squeue -h -j "$1" -o '%T' 2>/dev/null | head -1; }
job_node(){ squeue -h -j "$1" -o '%N' 2>/dev/null | head -1; }

# Prints UP/down and returns non-zero when unhealthy, so callers can gate on it.
probe(){
  local label="$1" host="$2" port="$3" path="$4" code
  code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://${host}:${port}${path}" 2>/dev/null)
  if [ "$code" = "200" ]; then
    printf '  %-24s %-30s UP (200)\n' "$label" "$path"
    return 0
  fi
  printf '  %-24s %-30s down (%s)\n' "$label" "$path" "${code:-000}"
  return 1
}

status_report(){
  local ultra_id stack_id ultra_node stack_node down=0
  ultra_id=$(job_id "$ULTRA_JOB"); stack_id=$(job_id "$STACK_JOB")
  ultra_node=$([ -n "$ultra_id" ] && job_node "$ultra_id")
  stack_node=$([ -n "$stack_id" ] && job_node "$stack_id")

  bold "== slurm =="
  squeue -u "$ME" -o '%.8i %.16j %.9T %.10M %.14R' 2>/dev/null | sed 's/^/  /'
  [ -z "$ultra_id$stack_id" ] && echo "  (no jobs)"

  bold ""
  bold "== supervisor model (job ${ultra_id:-none} on ${ultra_node:-?}) =="
  if [ -n "$ultra_node" ]; then
    probe "Nemotron 3 Ultra NIM" "$ultra_node" 8000 /v1/health/ready || down=1
  else
    echo "  not running"; down=1
  fi

  bold ""
  bold "== application stack (job ${stack_id:-none} on ${stack_node:-?}) =="
  if [ -n "$stack_node" ]; then
    probe "cuOpt"              "$stack_node" 5000 /cuopt/health     || down=1
    probe "cuOpt adapter"      "$stack_node" 8002 /health           || down=1
    probe "NeMo Guardrails"    "$stack_node" 8003 /v1/health        || down=1
    probe "OpenShell governor" "$stack_node" 8004 /api/v1/healthz   || down=1
    probe "Specialist NIM"     "$stack_node" 8010 /v1/health/ready  || true
    probe "WarehouseIQ UI"     "$stack_node" "$APP_PORT" /api/dashboard || down=1
  else
    echo "  not running"; down=1
  fi
  return $down
}

# Only the job id goes to stdout; everything human-readable goes to stderr so
# the caller can capture the id with $(...).
submit(){
  local name="$1" script="$2" out
  bold "" >&2
  bold "== starting ${name} ==" >&2
  out=$(sbatch "$script" 2>&1)
  echo "  $out" | sed 's/^/  /' >&2
  case "$out" in
    *"Submitted batch job"*) echo "$out" | grep -oE '[0-9]+$' ;;
    *)
      echo "  -> submission refused; not waiting on it." >&2
      echo "     If this mentions the GPU quota, the team allocation is the" >&2
      echo "     blocker, not this script." >&2
      return 1 ;;
  esac
}

# Follows the job log until the health check passes, so a slow model load is
# visible rather than looking like a hang.
wait_ready(){
  local label="$1" jobid="$2" logglob="$3" port="$4" path="$5"
  local waited=0 node state log code shown=0
  bold ""
  bold "== waiting for ${label} (job ${jobid}) =="
  while [ "$waited" -lt "$BOOT_TIMEOUT" ]; do
    state=$(job_state "$jobid")
    [ -z "$state" ] && { echo "  job ${jobid} left the queue - check ${logglob}"; return 1; }
    node=$(job_node "$jobid")
    if [ -n "$node" ] && [ "$state" = "RUNNING" ]; then
      code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://${node}:${port}${path}" 2>/dev/null)
      [ "$code" = "200" ] && { echo "  ${label} ready on ${node} after ${waited}s"; return 0; }
      log=$(ls -1t ${logglob} 2>/dev/null | head -1)
      if [ -n "$log" ]; then
        # Skip the model loader's per-layer spam; show progress lines only.
        tail -3 "$log" 2>/dev/null \
          | grep -vE 'routed_experts|Loading safetensors|AutoTuner|Capturing CUDA' \
          | sed "s/^/    /" && shown=1
      fi
      printf '  [%4ss] %s health=%s on %s\n' "$waited" "$label" "${code:-000}" "$node"
    else
      printf '  [%4ss] %s is %s\n' "$waited" "$label" "$state"
    fi
    sleep 20; waited=$((waited + 20))
  done
  echo "  timed out after ${BOOT_TIMEOUT}s waiting for ${label}"
  return 1
}

bold "WarehouseIQ - initial status"
status_report
initial=$?

if [ "$initial" -eq 0 ]; then
  bold ""
  bold "Everything is already running; nothing to start."
else
  started_ultra=""; started_stack=""
  [ -z "$(job_id "$ULTRA_JOB")" ] && started_ultra=$(submit "$ULTRA_JOB" deploy/ultra_serve.sbatch)
  [ -z "$(job_id "$STACK_JOB")" ] && started_stack=$(submit "$STACK_JOB" deploy/slurm_stack.sbatch)

  [ -n "${started_ultra:-}" ] && wait_ready "Nemotron 3 Ultra" "$started_ultra" "ultra-serve-*.log" 8000 /v1/health/ready
  [ -n "${started_stack:-}" ] && wait_ready "WarehouseIQ UI" "$started_stack" "warehouse-stack-*.log" "$APP_PORT" /api/dashboard

  bold ""
  bold "WarehouseIQ - final status"
  status_report
  initial=$?
fi

stack_node=$(job_node "$(job_id "$STACK_JOB")")
bold ""
if [ "$initial" -eq 0 ] && [ -n "$stack_node" ]; then
  bold "All services are up. The application is available on port ${APP_PORT} of ${stack_node}."
  echo
  echo "  From your laptop, forward the port:"
  echo "    ssh -N -L ${APP_PORT}:${stack_node}:${APP_PORT} ${SSH_HOST} -l <your-access-key>"
  echo
  echo "  Then open:  http://127.0.0.1:${APP_PORT}"
  exit 0
fi

bold "Some services are still down - see the status above and the job logs."
exit 1
