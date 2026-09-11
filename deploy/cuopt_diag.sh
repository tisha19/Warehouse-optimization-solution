#!/usr/bin/env bash
# Show cuOpt startup lines and anything that is not routine polling, which is
# what drowns out the interesting messages in `docker logs`.
set -uo pipefail

export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"

echo "=== cuOpt startup (first 60) ==="
docker logs warehouse-cuopt 2>&1 | head -60

echo
echo "=== non-polling lines (last 60) ==="
docker logs warehouse-cuopt 2>&1 \
  | grep -v 'GET /cuopt/solution' \
  | grep -v 'job_result returning json' \
  | grep -v 'GET /cuopt/health' \
  | tail -60

echo
echo "=== warnings and errors ==="
docker logs warehouse-cuopt 2>&1 | grep -Ei 'error|warn|exception|traceback|fail|gpu|cuda' | tail -40
