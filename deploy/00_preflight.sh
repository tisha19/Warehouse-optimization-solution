#!/usr/bin/env bash
# 00 - Verify the cluster node can host the NVIDIA stack. Changes nothing.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

fail=0
ok()   { printf '  [ OK ]  %s\n' "$1"; }
bad()  { printf '  [FAIL]  %s\n' "$1"; fail=1; }
warn() { printf '  [WARN]  %s\n' "$1"; }

echo "Preflight checks"
echo "----------------"

command -v docker >/dev/null 2>&1 && ok "docker present" || bad "docker not found"
command -v nvidia-smi >/dev/null 2>&1 && ok "nvidia-smi present" || bad "nvidia-smi not found (install the NVIDIA driver)"

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_count=$(nvidia-smi -L | wc -l)
  ok "GPUs detected: ${gpu_count}"
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/          /'
  driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  ok "driver ${driver}"
fi

# The container toolkit is what lets docker expose GPUs.
if docker info 2>/dev/null | grep -qi nvidia; then
  ok "nvidia container runtime registered with docker"
else
  warn "nvidia runtime not visible in 'docker info' - install nvidia-container-toolkit"
fi

if docker run --rm --gpus all "${NGC_REGISTRY}/nvidia/cuda:12.4.1-base-ubuntu22.04" nvidia-smi >/dev/null 2>&1; then
  ok "docker can access GPUs"
else
  warn "GPU passthrough smoke test failed (image pull may need 01_ngc_login.sh first)"
fi

[ -n "${NGC_API_KEY}" ] && ok "NGC_API_KEY set" || bad "NGC_API_KEY empty - edit deploy/config.env"

free_disk=$(df -Pk "${NIM_CACHE_DIR%/*}" 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}' || echo 0)
if [ "${free_disk:-0}" -ge 500 ]; then
  ok "free disk for model cache: ${free_disk} GiB"
else
  warn "only ${free_disk} GiB free - large NIM weights may not fit"
fi

for port in "${NIM_SUPERVISOR_PORT}" "${NIM_SUBAGENT_PORT}" "${CUOPT_SERVER_PORT}" "${CUOPT_ADAPTER_PORT}" "${GUARDRAILS_PORT}" "${OPENSHELL_PORT}" "${APP_PORT}"; do
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":${port}\$"; then
    warn "port ${port} already in use"
  fi
done

echo
[ "$fail" -eq 0 ] && echo "Preflight passed. Next: ./01_ngc_login.sh" || { echo "Preflight failed. Resolve [FAIL] items first."; exit 1; }
