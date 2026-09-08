#!/usr/bin/env bash
# 10 - Start the warehouse planner application.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
REPO_ROOT="$(cd .. && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python}"

# Ambient exports from earlier sessions must not shadow the generated .env.
unset NIM_BASE_URL NIM_MODEL NIM_API_KEY NEMO_GUARDRAILS_URL CUOPT_URL RAIL_API_URL LLM_NIM_URL 2>/dev/null || true

echo "Starting planner on http://${SERVICE_HOST}:${APP_PORT}"
exec "${PYTHON_BIN}" showcase_server.py --host 0.0.0.0 --port "${APP_PORT}"
