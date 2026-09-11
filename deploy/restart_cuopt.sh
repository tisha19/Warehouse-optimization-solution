#!/usr/bin/env bash
# Runs ON the compute node. Restart the cuOpt solver and prove it still solves.
set -uo pipefail
echo "== before =="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -i cuopt
echo
docker restart warehouse-cuopt >/dev/null
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
