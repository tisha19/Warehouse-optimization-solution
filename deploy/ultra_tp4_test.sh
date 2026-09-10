#!/usr/bin/env bash
# Experiment: can Nemotron 3 Ultra 550B-A55B serve at TP=4 on 4x B300?
# Frees every GPU first, then brings up Ultra alone and reports what it needs.
set -uo pipefail
# Mirrors the bring-up in cluster_up.sh; the module has to be loaded before the
# daemon helper will work, and its init scripts trip over set -u.
set +u
command -v module >/dev/null 2>&1 || { source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null; }
module load rootless-docker/1.75 2>/dev/null || true
set -u
export PATH="/cm/shared/apps/rootless-docker/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
cd "$HOME/gsh-team07/Warehouse-optimization-solution"

if ! docker info >/dev/null 2>&1; then
  echo "=== 0. starting rootless docker on $(hostname) ==="
  mkdir -p "$XDG_RUNTIME_DIR"
  rm -rf "$XDG_RUNTIME_DIR/dockerd-rootless"
  if command -v start_rootless_docker >/dev/null 2>&1; then
    start_rootless_docker
  else
    nohup dockerd-rootless.sh --experimental \
      --data-root="/raid/docker/tmp/docker-container-storage-$(id -u)" \
      --storage-driver overlay2 > /tmp/dockerd-ultra-test.log 2>&1 &
  fi
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
fi
docker info >/dev/null 2>&1 || {
  echo "rootless docker did not start" >&2
  tail -20 "$XDG_RUNTIME_DIR/dockerd.log" 2>/dev/null
  exit 1
}

ULTRA_IMAGE=${ULTRA_IMAGE:-nvcr.io/nim/nvidia/nemotron-3-ultra-550b-a55b:2.0.12}
CACHE=/raid/docker/tmp/nim-cache-$(id -un)
KEY=$(grep -m1 -oE 'nvapi-[A-Za-z0-9_-]+' .env)
PORT=8000

echo "=== 1. stopping GPU consumers ==="
docker rm -f warehouse-nim warehouse-cuopt warehouse-guardrails >/dev/null 2>&1
sleep 5
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader

echo
echo "=== 2. starting Ultra on devices 0-3, TP=4 ==="
docker run -d --name warehouse-nim-ultra --gpus '"device=0,1,2,3"' --shm-size=32g \
  -e NGC_API_KEY="$KEY" -e NIM_TENSOR_PARALLEL_SIZE=4 \
  -v "$CACHE:/opt/nim/.cache" -p ${PORT}:8000 "$ULTRA_IMAGE" || exit 1

echo
echo "=== 3. waiting for readiness (up to 90 min) ==="
start=$(date +%s)
for i in $(seq 1 540); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:${PORT}/v1/health/ready" || true)
  if [ "$code" = "200" ]; then
    echo "READY after $(( $(date +%s) - start ))s"
    break
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx warehouse-nim-ultra; then
    echo "CONTAINER EXITED after $(( $(date +%s) - start ))s"
    echo "--- last 60 log lines ---"
    docker logs --tail 60 warehouse-nim-ultra 2>&1
    exit 2
  fi
  if [ $((i % 20)) -eq 0 ]; then
    echo "  [$(( $(date +%s) - start ))s] health=$code  $(docker logs --tail 1 warehouse-nim-ultra 2>&1 | tail -c 160)"
  fi
  sleep 10
done

echo
echo "=== 4. served model ==="
curl -s -m 15 "http://127.0.0.1:${PORT}/v1/models" | head -c 600
echo

echo
echo "=== 5. GPU memory with Ultra resident ==="
nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv

echo
echo "=== 6. inference smoke test ==="
MODEL=$(curl -s -m 15 "http://127.0.0.1:${PORT}/v1/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
echo "model=$MODEL"
t0=$(date +%s%3N)
curl -s -m 120 "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}],\"max_tokens\":16}" \
  | head -c 700
echo
echo "latency_ms=$(( $(date +%s%3N) - t0 ))"
