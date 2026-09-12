#!/usr/bin/env bash
# Runs ON the compute node. Why is the cuOpt adapter not answering?
set -uo pipefail
echo "== containers =="
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -i adapter
echo
echo "== last 30 log lines =="
docker logs --tail 30 warehouse-cuopt-adapter 2>&1
echo
echo "== python in the image =="
docker run --rm --entrypoint python warehouse-cuopt-adapter --version 2>&1
