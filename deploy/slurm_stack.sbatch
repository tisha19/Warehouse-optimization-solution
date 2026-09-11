#!/usr/bin/env bash
# Runs the whole stack as a BATCH job so it survives SSH disconnects.
#   sbatch deploy/slurm_stack.sbatch
# Pin the node to reuse a warm image cache on /raid, which is node-local:
#   sbatch --nodelist=dgx02 deploy/slurm_stack.sbatch
#SBATCH --job-name=warehouse-stack
#SBATCH --partition=hackathon
#SBATCH --nodes=1
# Both Nemotron models run on NVIDIA's hosted endpoint, so cuOpt is the only
# thing left that needs a GPU. The QoS group limit of 4 is shared with the rest
# of the team, so asking for more than we use blocks everyone.
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=32
# The QoS ceiling is 30-12:00:00 and a running job's limit cannot be raised
# without admin rights, so the stack is sized for the maximum up front.
#SBATCH --time=30-00:00:00
#SBATCH --requeue
#SBATCH --output=%x-%j.log
#SBATCH --signal=B:TERM@60

set -uo pipefail
REPO="$HOME/gsh-team07/Warehouse-optimization-solution"
cd "$REPO"

# The monitor loop below shells out to docker, which is not on a batch job's
# default PATH and answers on a per-user rootless socket.
export PATH="/cm/shared/apps/rootless-docker/bin:$HOME/opt/node/bin:$PATH"
export DOCKER_HOST=${DOCKER_HOST:-unix:///raid/docker/tmp/xdg_runtime_dir_$(id -u)/docker.sock}

STACK_CONTAINERS="warehouse-guardrails warehouse-cuopt-adapter warehouse-cuopt"
cleanup(){ echo "[stack] shutting down"
  docker rm -f $STACK_CONTAINERS >/dev/null 2>&1
  pkill -f 'services\.openshell\.governor' >/dev/null 2>&1
  pkill -f 'showcase_server\.py' >/dev/null 2>&1; }
trap cleanup TERM EXIT

./deploy/cluster_up.sh

# cluster_up.sh chooses the ports and records them, so the monitor reads them
# back rather than assuming: probing the wrong port makes a healthy service look
# dead and, worse, leaves the real one dead because nothing notices.
STATE_FILE="deploy/state/stack.${USER:-$(id -un)}.env"
# shellcheck disable=SC1090
[ -f "$STATE_FILE" ] && . "$STATE_FILE"
OPENSHELL_PORT="${STACK_OPENSHELL_PORT:-28004}"
APP_PORT="${STACK_APP_PORT:-28090}"
ADAPTER_PORT="${STACK_ADAPTER_PORT:-28002}"
LOGS="/tmp/${USER:-$(id -un)}-warehouse"
mkdir -p "$LOGS"

echo "[stack] ready on $(hostname); holding allocation until the job ends"
echo "[stack] monitoring openshell on ${OPENSHELL_PORT}, UI on ${APP_PORT}"
while true; do
  sleep 60
  # The rootless docker daemon is owned by this batch step, so if the containers
  # have gone the whole stack is re-created rather than reported as down forever.
  missing=0
  for c in $STACK_CONTAINERS; do
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$c" || missing=1
  done
  if [ "$missing" = 1 ]; then
    echo "[stack] containers missing at $(date -Is) - re-running cluster_up.sh"
    ./deploy/cluster_up.sh
  fi
  # cuOpt answers /cuopt/health with 200 even when its solver worker has died
  # and is silently dropping every job, so probe an actual solve instead.
  if ! curl -sf -m 30 -o /dev/null "http://127.0.0.1:${ADAPTER_PORT}/selftest"; then
    echo "[stack] cuOpt cannot solve at $(date -Is) - restarting the container"
    docker restart warehouse-cuopt >/dev/null 2>&1
  fi
  # The Python services must be children of this batch step: anything launched
  # from an "srun --overlap" step is killed when that step exits.
  if ! curl -sf -m 5 -o /dev/null "http://127.0.0.1:${OPENSHELL_PORT}/api/v1/healthz"; then
    echo "[stack] restarting openshell governor at $(date -Is)"
    OPENSHELL_GOVERNOR_PORT=${OPENSHELL_PORT} nohup ./.venv/bin/python -m services.openshell.governor > "$LOGS/governor.log" 2>&1 &
  fi
  if ! curl -sf -m 5 -o /dev/null "http://127.0.0.1:${APP_PORT}/api/dashboard"; then
    echo "[stack] restarting planner UI at $(date -Is)"
    WAREHOUSE_ENV_PRECEDENCE=process nohup ./.venv/bin/python showcase_server.py --host 0.0.0.0 --port "${APP_PORT}" > "$LOGS/planner_ui.log" 2>&1 &
  fi
done
