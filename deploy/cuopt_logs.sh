#!/usr/bin/env bash
# Show recent cuOpt and adapter container logs. Rootless docker needs the same
# environment cluster_up.sh sets, which srun does not inherit.
set -uo pipefail

export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"

lines="${1:-40}"

echo "=== containers ==="
docker ps --format '{{.Names}}\t{{.Status}}'

echo
echo "=== warehouse-cuopt (last $lines) ==="
docker logs --tail "$lines" warehouse-cuopt 2>&1

echo
echo "=== warehouse-cuopt-adapter (last $lines) ==="
docker logs --tail "$lines" warehouse-cuopt-adapter 2>&1
