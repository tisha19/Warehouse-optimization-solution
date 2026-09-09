#!/usr/bin/env bash
# Where is the stack and what is healthy? Safe to run from the login node.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$REPO/deploy/state/stack.env"
echo "== slurm =="
command -v squeue >/dev/null || export PATH="/cm/local/apps/slurm/current/bin:$PATH"
squeue -u "$USER" -o "%.8i %.16j %.9T %.10M %.14R %b" 2>/dev/null || echo "  (squeue unavailable in this shell)"
[ -f "$STATE" ] || { echo "no deploy/state/stack.env yet"; exit 0; }
. "$STATE"
echo
echo "== stack node: ${STACK_NODE} (job ${STACK_JOB_ID}, started ${STACK_STARTED}, nim ${STACK_NIM_PROFILE:-?}) =="
probe(){ printf '  %-22s %-34s ' "$1" "$3"; c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://${STACK_NODE}:$2$3" 2>/dev/null); [ "$c" = "200" ] && echo "UP ($c)" || echo "down ($c)"; }
probe "cuOpt"            5000 /cuopt/health
probe "cuOpt adapter"    8002 /health
probe "NeMo Guardrails"  8003 /v1/health
probe "OpenShell"        8004 /api/v1/healthz
probe "NIM (${STACK_NIM_PROFILE:-?})" "${STACK_NIM_PORT:-8000}" /v1/health/ready
probe "Planner UI"       8090 /api/state
