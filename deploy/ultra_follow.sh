#!/usr/bin/env bash
# Follow the Ultra NIM container log plus periodic GPU memory, live.
export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export DOCKER_HOST="unix:///raid/docker/tmp/xdg_runtime_dir_$(id -u)/docker.sock"

( while true; do
    sleep 60
    printf '\n[gpu %s] %s\n\n' "$(date +%H:%M:%S)" \
      "$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | tr '\n' ' ')"
  done ) &
watcher=$!
trap 'kill $watcher 2>/dev/null' EXIT

docker logs -f --tail 40 warehouse-nim-ultra 2>&1
