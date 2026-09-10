#!/usr/bin/env bash
# Dedicated allocation to answer one question: does Nemotron 3 Ultra
# 550B-A55B serve at TP=4 on four B300s?
#   sbatch deploy/ultra_tp4_test.sbatch
#SBATCH --job-name=ultra-tp4-test
#SBATCH --partition=hackathon
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --time=04:00:00
#SBATCH --output=%x-%j.log

set -uo pipefail
cd "$HOME/gsh-team07/Warehouse-optimization-solution"
echo "[test] node=$(hostname) job=$SLURM_JOB_ID gpus=${SLURM_GPUS_ON_NODE:-?} cpus=${SLURM_JOB_CPUS_PER_NODE:-?}"

bash deploy/ultra_tp4_test.sh
rc=$?

echo
echo "[test] exit=$rc - tearing down"
export PATH="/cm/shared/apps/rootless-docker/bin:$PATH"
export DOCKER_HOST="unix:///raid/docker/tmp/xdg_runtime_dir_$(id -u)/docker.sock"
docker rm -f warehouse-nim-ultra >/dev/null 2>&1
exit $rc
