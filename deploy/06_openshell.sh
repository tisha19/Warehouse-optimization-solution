#!/usr/bin/env bash
# 06 - Start the OpenShell policy gateway.
#
# OpenShell is distributed separately from the public NGC catalog. Set
# OPENSHELL_IMAGE in config.env (or export it here) to the image your
# entitlement provides. The planner calls OPENSHELL_URL to authorise tool use;
# when it is absent the local allowlist in tools/governance.py applies instead,
# which still blocks un-approved WMS writes.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
source ./_lib.sh

: "${OPENSHELL_IMAGE:=}"

if [ -z "${OPENSHELL_IMAGE}" ]; then
  echo "OPENSHELL_IMAGE is not set - skipping the OpenShell gateway."
  echo "The planner will enforce the local tool allowlist instead."
  echo "Next: ./07_enterprise_gateway.sh"
  exit 0
fi

start_container warehouse-openshell \
  --network host \
  -e OPENSHELL_PORT="${OPENSHELL_PORT}" \
  -e OPENSHELL_SANDBOX_NAME="${OPENSHELL_SANDBOX_NAME:-warehouse-sandbox}" \
  "${OPENSHELL_IMAGE}"

wait_for_http "http://${SERVICE_HOST}:${OPENSHELL_PORT}/health" 300 "OpenShell" || true

echo "Next: ./07_enterprise_gateway.sh"
