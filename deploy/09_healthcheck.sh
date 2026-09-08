#!/usr/bin/env bash
# 09 - Report exactly which dependencies are wired, then run the offline harness.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd .. && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python}"

echo "=== Dependency wiring ==="
"${PYTHON_BIN}" -m tools.wiring_report --timeout 8

echo "=== Constraint and governance harness ==="
"${PYTHON_BIN}" -m unittest discover -s tests

echo
echo "Anything reported NOT WIRED degrades to the labelled fallback shown above."
echo "Next: ./deploy/10_run_app.sh"
