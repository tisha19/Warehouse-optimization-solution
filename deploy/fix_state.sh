#!/usr/bin/env bash
# Rewrite the stack state file from what Slurm actually reports.
#
# A job that is being cancelled can re-run cluster_up.sh on its way out and
# stamp the state file with its own (dying) node, after a replacement job has
# already recorded the live one. Everything that reads the file then points at
# a node with nothing on it.
#
#   ./deploy/fix_state.sh            # repair from the newest running job
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
export PATH="/cm/local/apps/slurm/current/bin:$PATH"
export SLURM_CONF="${SLURM_CONF:-/cm/shared/apps/slurm/etc/slurm/slurm.conf}"

ME="$(id -un)"
STATE_FILE="deploy/state/stack.$ME.env"
[ -f "$STATE_FILE" ] || { echo "no state file at $STATE_FILE" >&2; exit 1; }

read -r JOB NODE <<<"$(squeue -u "$ME" -h -t RUNNING -n warehouse-stack -o '%i %N' | tail -1)"
[ -n "${JOB:-}" ] && [ -n "${NODE:-}" ] || { echo "no running warehouse-stack job" >&2; exit 1; }

python3 - "$STATE_FILE" "$JOB" "$NODE" <<'PYEOF'
import pathlib, sys
path, job, node = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = []
for line in path.read_text().splitlines():
    if line.startswith("STACK_NODE="):
        line = f"STACK_NODE={node}"
    elif line.startswith("STACK_JOB_ID="):
        line = f"STACK_JOB_ID={job}"
    lines.append(line)
path.write_text("\n".join(lines) + "\n")
print(f"state file now points at {node} (job {job})")
PYEOF
