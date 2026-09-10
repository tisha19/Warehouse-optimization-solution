#!/usr/bin/env python3
"""Normalise CRLF line endings introduced by copying files from Windows."""
import pathlib
import sys

for name in sys.argv[1:]:
    path = pathlib.Path(name)
    if not path.is_file():
        print(f"missing {name}")
        continue
    raw = path.read_bytes()
    if b"\r\n" in raw:
        path.write_bytes(raw.replace(b"\r\n", b"\n"))
        print(f"fixed {name}")
    else:
        print(f"clean {name}")
