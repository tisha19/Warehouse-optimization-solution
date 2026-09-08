#!/usr/bin/env bash
# Runs the whole self-hosted stack as a BATCH job so it survives SSH disconnects.
#   sbatch deploy/slurm_stack.sbatch
#SBATCH --job-name=warehouse-stack
#SBATCH --partition=hackathon
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=64
#SBATCH --time=08:00:00
#SBATCH --output=%x-%j.log
#SBATCH --signal=B:TERM@60

set -uo pipefail
REPO="$HOME/gsh-team07/Warehouse-optimization-solution"
cd "$REPO"

cleanup(){ echo "[stack] shutting down"; docker rm -f warehouse-nim-supervisor warehouse-nim-subagent warehouse-guardrails warehouse-cuopt-adapter warehouse-cuopt >/dev/null 2>&1; pkill -f services.openshell.governor >/dev/null 2>&1; }
trap cleanup TERM EXIT

./deploy/cluster_up.sh

echo "[stack] ready on $(hostname); holding allocation until the job ends"
while true; do
  sleep 300
  # Restart anything that died so a crash does not silently drain the stack.
  for c in warehouse-cuopt warehouse-cuopt-adapter warehouse-guardrails warehouse-nim-supervisor warehouse-nim-subagent; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
      echo "[stack] $c is down at $(date -Is)"
    fi
  done
done
