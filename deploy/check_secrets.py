#!/usr/bin/env python3
"""Fail if a credential looks like it reached a tracked file.

Runs against `git ls-files`, so anything gitignored is out of scope by
construction. Prints only the file and line, never the matched text.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

PATTERNS = {
    "nvidia api key": re.compile(r"nvapi-[A-Za-z0-9_\-]{16,}"),
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{24,}"),
    "private key block": re.compile(r"BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".lock"}

tracked = subprocess.run(
    ["git", "ls-files"], capture_output=True, text=True, check=True
).stdout.split()

findings = []
for name in tracked:
    path = pathlib.Path(name)
    if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for line_no, line in enumerate(text.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(f"{name}:{line_no}: {label}")

if ".env" in tracked:
    findings.append(".env is tracked; it must stay gitignored")

print(f"scanned {len(tracked)} tracked files")
if findings:
    print("POSSIBLE CREDENTIAL IN TRACKED FILE:")
    for item in findings:
        print(f"  {item}")
    sys.exit(1)
print("clean: no credential patterns in tracked files")
