#!/usr/bin/env bash
# Keep Nemotron 3 Ultra 550B-A55B serving on 4x B300 and hold the allocation.
# Pinned to the node that already has the model cached so it starts warm.
#   sbatch --dependency=afterany:<testjob> deploy/ultra_serve.sbatch
#SBATCH --job-name=ultra-serve
#SBATCH --partition=hackathon
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --time=30-00:00:00
#SBATCH --requeue
#SBATCH --output=%x-%j.log

set -uo pipefail
cd "$HOME/gsh-team07/Warehouse-optimization-solution"

set +u
command -v module >/dev/null 2>&1 || { source /etc/profile.d/modules.sh 2>/dev/null || source /usr/share/lmod/lmod/init/bash 2>/dev/null; }
module load rootless-docker/1.75 2>/dev/null || true
set -u
export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export XDG_RUNTIME_DIR="/raid/docker/tmp/xdg_runtime_dir_$(id -u)"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"

ULTRA_IMAGE=${ULTRA_IMAGE:-nvcr.io/nim/nvidia/nemotron-3-ultra-550b-a55b:2.0.12}
CACHE=/raid/docker/tmp/nim-cache-$(id -un)
KEY=$(grep -m1 -oE 'nvapi-[A-Za-z0-9_-]+' .env)
PORT=8000

echo "[serve] node=$(hostname) job=$SLURM_JOB_ID gpus=${SLURM_GPUS_ON_NODE:-?}"

if ! docker info >/dev/null 2>&1; then
  echo "[serve] starting rootless docker"
  mkdir -p "$XDG_RUNTIME_DIR"; rm -rf "$XDG_RUNTIME_DIR/dockerd-rootless"
  if command -v start_rootless_docker >/dev/null 2>&1; then start_rootless_docker
  else nohup dockerd-rootless.sh --experimental \
         --data-root="/raid/docker/tmp/docker-container-storage-$(id -u)" \
         --storage-driver overlay2 > /tmp/dockerd-ultra-serve.log 2>&1 & fi
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
fi
docker info >/dev/null 2>&1 || { echo "[serve] rootless docker did not start" >&2; exit 1; }

docker rm -f warehouse-nim-ultra >/dev/null 2>&1
docker run -d --name warehouse-nim-ultra --gpus '"device=0,1,2,3"' --shm-size=32g \
  -e NGC_API_KEY="$KEY" -e NIM_TENSOR_PARALLEL_SIZE=4 \
  -v "$CACHE:/opt/nim/.cache" -p ${PORT}:8000 "$ULTRA_IMAGE" || exit 1

start=$(date +%s)
for i in $(seq 1 540); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:${PORT}/v1/health/ready" || true)
  [ "$code" = "200" ] && { echo "[serve] READY after $(( $(date +%s) - start ))s"; break; }
  docker ps --format '{{.Names}}' | grep -qx warehouse-nim-ultra || {
    echo "[serve] container exited"; docker logs --tail 60 warehouse-nim-ultra; exit 2; }
  [ $((i % 20)) -eq 0 ] && echo "[serve] $(( $(date +%s) - start ))s health=$code"
  sleep 10
done

nvidia-smi --query-gpu=index,memory.total,memory.used --format=csv
curl -s -m 15 "http://127.0.0.1:${PORT}/v1/models" | head -c 400; echo

echo "[serve] holding allocation; Ultra stays up until this job ends"
trap 'docker rm -f warehouse-nim-ultra >/dev/null 2>&1' TERM EXIT
while true; do
  sleep 300
  curl -sf -m 5 -o /dev/null "http://127.0.0.1:${PORT}/v1/health/ready" && continue
  echo "[serve] NIM not answering at $(date -Is)"
  # Only rebuild the container if it has actually gone; a slow reload is not a crash.
  docker ps --format '{{.Names}}' | grep -qx warehouse-nim-ultra && continue
  echo "[serve] container gone - restarting"
  docker rm -f warehouse-nim-ultra >/dev/null 2>&1
  docker run -d --name warehouse-nim-ultra --gpus '"device=0,1,2,3"' --shm-size=32g \
    -e NGC_API_KEY="$KEY" -e NIM_TENSOR_PARALLEL_SIZE=4 \
    -v "$CACHE:/opt/nim/.cache" -p ${PORT}:8000 "$ULTRA_IMAGE" >/dev/null \
    || echo "[serve] restart failed at $(date -Is)"
done
