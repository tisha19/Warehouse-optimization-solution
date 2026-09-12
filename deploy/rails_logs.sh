#!/usr/bin/env bash
# Recent guardrails container log, filtered to what matters. Runs ON the node.
set -uo pipefail

export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"

lines="${1:-60}"
echo "=== status ==="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -i rails || echo "(no guardrails container)"

echo
echo "=== errors and warnings ==="
docker logs warehouse-guardrails 2>&1 | grep -Ei 'error|warn|exception|traceback|timeout|refus|unauthor|401|403|429|500' | tail -30

echo
echo "=== last $lines lines ==="
docker logs --tail "$lines" warehouse-guardrails 2>&1
