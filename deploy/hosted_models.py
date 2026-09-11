#!/usr/bin/env python3
"""List models on NVIDIA's hosted endpoint, optionally filtered.

Usage: hosted_models.py [substring ...]
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request


def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    file = pathlib.Path(path)
    if file.is_file():
        for line in file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


env = load_env()
base = env["NIM_SUPERVISOR_BASE_URL"].rstrip("/")
key = env["NIM_SUPERVISOR_API_KEY"]

request = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
with urllib.request.urlopen(request, timeout=60) as response:
    models = [item["id"] for item in json.load(response)["data"]]

needles = [arg.lower() for arg in sys.argv[1:]]
print(f"total models: {len(models)}")
if not needles:
    for name in sorted(models):
        print(f"  {name}")
    raise SystemExit(0)

for needle in needles:
    hits = sorted(name for name in models if needle in name.lower())
    print(f"\n=== matching {needle!r}: {len(hits)} ===")
    for name in hits:
        print(f"  {name}")
