#!/usr/bin/env bash
# Shared helpers for the numbered deployment scripts.
set -euo pipefail

# start_container NAME [docker run args...] IMAGE
start_container() {
  local name="$1"; shift
  if [ -n "$(docker ps -aq -f name="^${name}$")" ]; then
    echo "Removing previous ${name}"
    docker rm -f "${name}" >/dev/null
  fi
  echo "Starting ${name}"
  eval docker run -d --name "${name}" --restart unless-stopped "$@"
}

# wait_for_http URL TIMEOUT_SECONDS LABEL
wait_for_http() {
  local url="$1" timeout="$2" label="$3" waited=0
  printf 'Waiting for %s at %s ' "${label}" "${url}"
  while [ "${waited}" -lt "${timeout}" ]; do
    if curl -sf -o /dev/null "${url}"; then
      printf ' ready (%ss)\n' "${waited}"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
    printf '.'
  done
  printf '\n'
  echo "Timed out after ${timeout}s. Inspect logs with: docker logs -f <container>"
  return 1
}
