#!/usr/bin/env bash
# Rebuild the cuOpt slotting adapter image and restart the container on the compute node.
set -euo pipefail
# srun hands us a minimal environment, so the standard system paths are re-added.
export PATH="/cm/shared/apps/rootless-docker/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
# The stack runs under rootless docker; take the socket from the live daemon.
if [ -z "${DOCKER_HOST:-}" ]; then
  rk=$(pgrep -u "$(id -u)" -x rootlesskit | head -1 || true)
  if [ -n "$rk" ]; then
    export DOCKER_HOST=$(tr '\0' '\n' < "/proc/$rk/environ" | sed -n 's/^DOCKER_HOST=//p' | head -1)
  fi
fi
[ -n "${DOCKER_HOST:-}" ] || { echo "rootless docker daemon not found on this node" >&2; exit 1; }
echo "using DOCKER_HOST=$DOCKER_HOST"
cd "$HOME/gsh-team07/Warehouse-optimization-solution"

# cluster_up.sh chooses the ports and records them. Hardcoding the defaults here
# restarts the adapter on a port nothing is talking to, which looks like a
# healthy container and a dead service.
STATE_FILE="deploy/state/stack.${USER:-$(id -un)}.env"
# shellcheck disable=SC1090
[ -f "$STATE_FILE" ] && . "$STATE_FILE"
ADAPTER_PORT="${STACK_ADAPTER_PORT:-28002}"
CUOPT_PORT="${STACK_CUOPT_PORT:-25000}"
echo "adapter port ${ADAPTER_PORT}, cuOpt on ${CUOPT_PORT}"

docker build -q -t warehouse-cuopt-adapter ./deploy/cuopt_adapter
docker rm -f warehouse-cuopt-adapter >/dev/null 2>&1 || true
docker run -d --name warehouse-cuopt-adapter --network host \
  -e CUOPT_SERVER_URL="http://127.0.0.1:${CUOPT_PORT}" -e ADAPTER_PORT="${ADAPTER_PORT}" \
  warehouse-cuopt-adapter >/dev/null
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${ADAPTER_PORT}/health" || true)
  [ "$code" = "200" ] && { echo "adapter health: 200 on ${ADAPTER_PORT}"; exit 0; }
  sleep 2
done
echo "adapter did not become healthy on ${ADAPTER_PORT}" >&2
exit 1
