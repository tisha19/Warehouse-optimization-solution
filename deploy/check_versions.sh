#!/usr/bin/env bash
# Report the real latest versions on PyPI and what the venv currently has.
set -uo pipefail
cd "$HOME/gsh-team07/Warehouse-optimization-solution"
PY=./.venv/bin/python

echo "=== interpreter ==="
$PY -V

echo
echo "=== latest on PyPI vs installed ==="
for pkg in deepagents langchain langchain-core langgraph langchain-nvidia-ai-endpoints langchain-openai; do
  latest=$($PY - "$pkg" <<'EOF'
import json, sys, urllib.request
pkg = sys.argv[1]
try:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30) as r:
        print(json.load(r)["info"]["version"])
except Exception as exc:
    print(f"lookup-failed ({type(exc).__name__})")
EOF
)
  installed=$($PY -c "
import importlib.metadata as m
try: print(m.version('$pkg'))
except Exception: print('-')
" 2>/dev/null)
  printf '  %-34s latest=%-12s installed=%s\n' "$pkg" "$latest" "$installed"
done
