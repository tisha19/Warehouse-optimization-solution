#!/usr/bin/env bash
# Runs the whole self-hosted stack as a BATCH job so it survives SSH disconnects.
#   sbatch deploy/slurm_stack.sbatch
#SBATCH --job-name=warehouse-stack
#SBATCH --partition=hackathon
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --time=08:00:00
#SBATCH --output=%x-%j.log
#SBATCH --signal=B:TERM@60

set -uo pipefail
REPO="$HOME/gsh-team07/Warehouse-optimization-solution"
cd "$REPO"

# The monitor loop below shells out to docker, which is not on a batch job's
# default PATH and answers on a per-user rootless socket.
export PATH="/cm/shared/apps/rootless-docker/bin:$HOME/opt/node/bin:$PATH"
export DOCKER_HOST=${DOCKER_HOST:-unix:///raid/docker/tmp/xdg_runtime_dir_$(id -u)/docker.sock}

STACK_CONTAINERS="warehouse-nim warehouse-guardrails warehouse-cuopt-adapter warehouse-cuopt"
cleanup(){ echo "[stack] shutting down"
  docker rm -f $STACK_CONTAINERS >/dev/null 2>&1
  pkill -f 'services\.openshell\.governor' >/dev/null 2>&1
  pkill -f 'showcase_server\.py' >/dev/null 2>&1; }
trap cleanup TERM EXIT

./deploy/cluster_up.sh

echo "[stack] ready on $(hostname); holding allocation until the job ends"
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
  # The Python services must be children of this batch step: anything launched
  # from an "srun --overlap" step is killed when that step exits.
  if ! curl -sf -m 5 -o /dev/null http://127.0.0.1:8004/api/v1/healthz; then
    echo "[stack] restarting openshell governor at $(date -Is)"
    OPENSHELL_GOVERNOR_PORT=8004 nohup ./.venv/bin/python -m services.openshell.governor > /tmp/governor.log 2>&1 &
  fi
  if ! curl -sf -m 5 -o /dev/null "http://127.0.0.1:${APP_PORT:-8090}/api/dashboard"; then
    echo "[stack] restarting planner UI at $(date -Is)"
    nohup ./.venv/bin/python showcase_server.py --host 0.0.0.0 --port "${APP_PORT:-8090}" > /tmp/planner_ui.log 2>&1 &
  fi
done
