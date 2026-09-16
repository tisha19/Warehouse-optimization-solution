#!/usr/bin/env bash
# Where is the stack and what is healthy? Safe to run from the login node.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Per-user state: the checkout is on shared storage and teammates run the same
# stack, so the unqualified file would describe whichever of us started last.
STATE="$REPO/deploy/state/stack.$USER.env"
[ -f "$STATE" ] || STATE="$REPO/deploy/state/stack.env"
echo "== slurm =="
command -v squeue >/dev/null || export PATH="/cm/local/apps/slurm/current/bin:$PATH"
export SLURM_CONF="${SLURM_CONF:-/cm/shared/apps/slurm/etc/slurm/slurm.conf}"
squeue -u "$USER" -o "%.8i %.16j %.9T %.10M %.14R %b" 2>/dev/null || echo "  (squeue unavailable in this shell)"
[ -f "$STATE" ] || { echo "no deploy/state/stack.$USER.env yet"; exit 0; }
. "$STATE"
echo
echo "== stack node: ${STACK_NODE} (job ${STACK_JOB_ID}, started ${STACK_STARTED}) =="
echo "   models: ${STACK_SUPERVISOR_MODEL:-?} (orchestrator) / ${STACK_SPECIALIST_MODEL:-?} (specialists), hosted by NVIDIA"
probe(){ printf '  %-22s %-34s ' "$1" "$3"; c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://${STACK_NODE}:$2$3" 2>/dev/null); [ "$c" = "200" ] && echo "UP ($c)" || echo "down ($c)"; }
probe "cuOpt"            "${STACK_CUOPT_PORT:-25000}"      /cuopt/health
probe "cuOpt adapter"    "${STACK_ADAPTER_PORT:-28002}"    /health
probe "NeMo Guardrails"  "${STACK_GUARDRAILS_PORT:-28003}" /v1/health
probe "OpenShell"        "${STACK_OPENSHELL_PORT:-28004}"  /api/v1/healthz
probe "WarehouseIQ UI"   "${STACK_APP_PORT:-28090}"        /api/dashboard
