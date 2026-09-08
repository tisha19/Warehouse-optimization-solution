#!/usr/bin/env bash
# 01 - Authenticate to the NVIDIA container registry and pre-pull images.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

[ -n "${NGC_API_KEY}" ] || { echo "NGC_API_KEY is empty. Edit deploy/config.env"; exit 1; }

echo "Logging in to ${NGC_REGISTRY}"
echo "${NGC_API_KEY}" | docker login "${NGC_REGISTRY}" --username '$oauthtoken' --password-stdin

mkdir -p "${NIM_CACHE_DIR}"
chmod 777 "${NIM_CACHE_DIR}"

# Pulling now surfaces tag/entitlement errors before the run scripts.
for image in "${NIM_SUPERVISOR_IMAGE}" "${NIM_SUBAGENT_IMAGE}" "${CUOPT_IMAGE}"; do
  echo "Pulling ${image}"
  if ! docker pull "${image}"; then
    echo "  Pull failed. Confirm the exact tag in the NGC catalog and update deploy/config.env."
    exit 1
  fi
done

echo "Login and pulls complete. Next: ./02_nim_supervisor.sh"
