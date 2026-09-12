#!/usr/bin/env python3
"""Normalise line endings introduced by copying files from Windows.

Rewriting CRLF alone is not enough. A file can also end up with bare CR bytes,
and those are invisible in almost all output yet break shell scripts in a way
that is very hard to read: a heredoc terminator written as "EOF\r" never
matches its opener, so the heredoc swallows the rest of the file and bash
reports nothing more useful than "unexpected end of file".
"""
import pathlib
import sys

missing = False
for name in sys.argv[1:]:
    path = pathlib.Path(name)
    if not path.is_file():
        print(f"missing {name}")
        missing = True
        continue
    raw = path.read_bytes()
    fixed = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if fixed == raw:
        print(f"clean {name}")
        continue
    crlf = raw.count(b"\r\n")
    bare_cr = raw.count(b"\r") - crlf
    path.write_bytes(fixed)
    print(f"fixed {name} (crlf={crlf} bare_cr={bare_cr})")

sys.exit(1 if missing else 0)
