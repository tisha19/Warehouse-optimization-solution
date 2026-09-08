#!/usr/bin/env bash
# 07 - Serve warehouse business data (WMS / ERP / forecast).
#
# THIS IS THE ONE COMPONENT THAT IS NOT REAL unless you point it at real systems.
# If WMS_URL/ERP_URL/FORECAST_URL in config.env already reference your production
# APIs, this script does nothing. Otherwise it starts the synthetic gateway so the
# planner has data to reason over, and the UI labels the result accordingly.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

REPO_ROOT="$(cd .. && pwd)"
GATEWAY_HOST="http://${SERVICE_HOST}:${ENTERPRISE_GATEWAY_PORT}"

if [ "${WMS_URL}" != "${GATEWAY_HOST}" ]; then
  echo "WMS_URL points at ${WMS_URL} - assuming a real WMS. Skipping the synthetic gateway."
  echo "Next: ./08_write_env.sh"
  exit 0
fi

echo "Starting the synthetic enterprise gateway on port ${ENTERPRISE_GATEWAY_PORT}"
echo "WARNING: this serves generated warehouse data, not production records."

pkill -f "mocks.enterprise_services --port ${ENTERPRISE_GATEWAY_PORT}" 2>/dev/null || true

cd "${REPO_ROOT}"
nohup python -m mocks.enterprise_services --port "${ENTERPRISE_GATEWAY_PORT}" --seed 7 \
  > "${REPO_ROOT}/logs_enterprise_gateway.txt" 2>&1 &

sleep 3
if curl -sf -X POST -H 'Content-Type: application/json' -d '{}' \
     "${GATEWAY_HOST}/erp/sku-master" >/dev/null; then
  echo "Enterprise gateway responding."
else
  echo "Gateway did not respond - see logs_enterprise_gateway.txt"
  exit 1
fi

echo "Next: ./08_write_env.sh"
