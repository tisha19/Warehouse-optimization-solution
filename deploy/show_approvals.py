#!/usr/bin/env python3
"""Show the tail of the approval store, so a superseded recommendation is visible."""
from __future__ import annotations

import json
import pathlib

rows = json.loads(pathlib.Path("results/approvals.json").read_text())
print(f"{len(rows)} approval(s); last 6:")
for row in rows[-6:]:
    print(
        f"  {row['approval_id']}  {row['status']:<9} {len(row['moves']):>3} moves  "
        f"round={row.get('validation', {}).get('approved_round')}  {row.get('reason', '')}"
    )
