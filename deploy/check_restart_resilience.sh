#!/usr/bin/env bash
# Restart cuOpt while a solve is in flight and report whether it still returned.
# This is the case the watchdog creates: the adapter must ride it out.
set -uo pipefail

export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"

cd "$(dirname "$0")/.."

echo "starting a solve in the background ..."
PYTHONPATH="$PWD" ./.venv/bin/python deploy/check_solver.py 1 > /tmp/solve_under_restart.out 2>&1 &
solver=$!

sleep 2
echo "restarting cuOpt underneath it ..."
docker restart warehouse-cuopt >/dev/null 2>&1 || echo "restart failed"

wait "$solver"
echo "--- solve result ---"
cat /tmp/solve_under_restart.out
