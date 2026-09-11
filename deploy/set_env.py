#!/usr/bin/env python3
"""Upsert KEY=VALUE lines read from stdin into .env, leaving everything else alone.

Reading from stdin keeps secrets off the command line and out of shell history.
"""
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
lines = path.read_text().splitlines() if path.is_file() else []

updates: dict[str, str] = {}
for raw in sys.stdin.read().splitlines():
    raw = raw.strip()
    if not raw or raw.startswith("#") or "=" not in raw:
        continue
    name, _, value = raw.partition("=")
    updates[name.strip()] = value.strip()

out: list[str] = []
seen: set[str] = set()
for line in lines:
    name = line.split("=", 1)[0].strip() if "=" in line else ""
    if name in updates:
        out.append(f"{name}={updates[name]}")
        seen.add(name)
    else:
        out.append(line)

for name, value in updates.items():
    if name not in seen:
        out.append(f"{name}={value}")

path.write_text("\n".join(out) + "\n")
print(f"updated {len(updates)} key(s) in {path}: {', '.join(sorted(updates))}")
