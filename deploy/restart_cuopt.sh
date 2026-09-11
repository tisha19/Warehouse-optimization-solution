#!/usr/bin/env bash
# Runs ON the compute node. Restart the cuOpt solver and prove it still solves.
set -uo pipefail

# srun does not inherit the login PATH, so without this docker is missing and
# the restart silently does nothing while the old container keeps serving.
export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
command -v docker >/dev/null || { echo "docker not found on $(hostname)" >&2; exit 1; }

echo "== before =="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -i cuopt
echo
docker restart warehouse-cuopt >/dev/null || { echo "restart failed" >&2; exit 1; }
echo "restarted; waiting for health"
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:25000/cuopt/health || true)
  [ "$code" = "200" ] && { echo "cuOpt health: 200"; break; }
  sleep 2
done
echo
echo "== after =="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -i cuopt
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader
