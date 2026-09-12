#!/usr/bin/env python3
"""Print .env keys with their values masked, so config can be checked safely."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
if not path.is_file():
    print(f"{path} does not exist")
    raise SystemExit(0)

SECRET_HINTS = ("key", "token", "secret", "password")
for line in path.read_text().splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    name, _, value = stripped.partition("=")
    if any(hint in name.lower() for hint in SECRET_HINTS) and value:
        value = f"<set, {len(value)} chars, ends {value[-4:]}>"
    print(f"{name}={value}")
