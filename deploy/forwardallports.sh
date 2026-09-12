#!/usr/bin/env bash
# Forward every WarehouseIQ endpoint from the cluster to your own machine.
#
# Run this on YOUR laptop, not on the cluster. Port forwarding is set up by the
# SSH client, so each team member runs it themselves with their own access key.
#
#   ./deploy/forwardallports.sh <your-access-key>
#
# Then open http://127.0.0.1:28090 for the UI. The other services are forwarded
# too, so the solver, rails and governor APIs are reachable from local tools.
#
# Options:
#   --owner <user>    whose stack to forward (default: the shared stack owner)
#   --offset <n>      shift every local port by n, if something local already
#                     holds one of them. Remote ports are unchanged.
#   --print           show the ssh command and the URL table, then exit
#
# Works with any OpenSSH client: macOS, Linux, WSL, or Git Bash on Windows.
# Leave it running; Ctrl-C closes the tunnels.
#
# Related:
#   deploy/runall.sh       - start the stack (run that on the cluster first)
#   deploy/stack_status.sh - check what is healthy
set -uo pipefail

SSH_HOST="${WAREHOUSE_SSH_HOST:-ssh.axisapps.io}"
OWNER="${WAREHOUSE_STACK_OWNER:-gsh-vzcpx}"
REPO_PATH="${WAREHOUSE_REPO_PATH:-gsh-team07/Warehouse-optimization-solution}"
OFFSET=0
PRINT_ONLY=0
KEY="${WAREHOUSE_SSH_KEY:-}"

usage(){ sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --owner)  OWNER="$2"; shift 2 ;;
    --offset) OFFSET="$2"; shift 2 ;;
    --print)  PRINT_ONLY=1; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "unknown option: $1" >&2; usage 1 ;;
    *) KEY="$1"; shift ;;
  esac
done

[ -n "$KEY" ] || { echo "error: no access key given" >&2; usage 1; }
case "$OFFSET" in (*[!0-9]*) echo "error: --offset must be a number" >&2; exit 1 ;; esac

# The stack records where it landed in a per-user state file on shared storage,
# so we read the owner's rather than guessing the node or the ports.
echo "reading the stack state from $OWNER ..."
STATE=$(ssh -o BatchMode=yes "$SSH_HOST" -l "$KEY" \
  "cat /storage/hackathon_teams/${REPO_PATH#*/}/deploy/state/stack.${OWNER}.env 2>/dev/null \
   || cat \$HOME/${REPO_PATH}/deploy/state/stack.${OWNER}.env 2>/dev/null" 2>/dev/null)

if [ -z "$STATE" ]; then
  echo "error: no stack state for '$OWNER'." >&2
  echo "       Has anyone run ./deploy/runall.sh on the cluster yet?" >&2
  echo "       If someone else is hosting it, pass --owner <their-user>." >&2
  exit 1
fi

get(){ printf '%s\n' "$STATE" | grep -m1 -E "^$1=" | cut -d= -f2- ; }
NODE=$(get STACK_NODE)
[ -n "$NODE" ] || { echo "error: state file has no STACK_NODE" >&2; exit 1; }

# A job being cancelled can re-stamp the state file with its own dying node
# after a replacement has recorded the live one, so Slurm is asked to confirm.
LIVE_NODE=$(ssh -o BatchMode=yes "$SSH_HOST" -l "$KEY" \
  "PATH=/cm/local/apps/slurm/current/bin:\$PATH \
   SLURM_CONF=/cm/shared/apps/slurm/etc/slurm/slurm.conf \
   squeue -u '$OWNER' -h -t RUNNING -n warehouse-stack -o '%N' 2>/dev/null | tail -1" 2>/dev/null | tr -d '[:space:]')
if [ -n "$LIVE_NODE" ] && [ "$LIVE_NODE" != "$NODE" ]; then
  echo "state file says $NODE but the stack job is on $LIVE_NODE - using $LIVE_NODE"
  NODE="$LIVE_NODE"
fi

# label:remote-port pairs, in the order they are shown.
SERVICES="
WarehouseIQ UI:$(get STACK_APP_PORT):28090
cuOpt solver:$(get STACK_CUOPT_PORT):25000
cuOpt adapter:$(get STACK_ADAPTER_PORT):28002
NeMo Guardrails:$(get STACK_GUARDRAILS_PORT):28003
OpenShell governor:$(get STACK_OPENSHELL_PORT):28004
"

FORWARDS=""
TABLE=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  label=${line%%:*}; rest=${line#*:}
  remote=${rest%%:*}; fallback=${rest#*:}
  [ -n "$remote" ] || remote="$fallback"
  local_port=$((remote + OFFSET))
  FORWARDS="$FORWARDS -L ${local_port}:${NODE}:${remote}"
  TABLE="${TABLE}  $(printf '%-20s http://127.0.0.1:%-6s -> %s:%s' "$label" "$local_port" "$NODE" "$remote")
"
done <<EOF
$SERVICES
EOF

echo
echo "stack owner : $OWNER"
echo "stack node  : $NODE"
echo
printf '%s' "$TABLE"
echo

if [ "$PRINT_ONLY" = 1 ]; then
  echo "ssh -N$FORWARDS $SSH_HOST -l $KEY"
  exit 0
fi

echo "forwarding - leave this running, Ctrl-C to stop"
echo
# ServerAlive keeps the tunnels from dying silently on an idle link.
exec ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=4 \
  $FORWARDS "$SSH_HOST" -l "$KEY"
