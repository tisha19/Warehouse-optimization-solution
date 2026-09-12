#!/usr/bin/env python3
"""How long does cuOpt actually need as the move cap grows?

The orchestrator is capped at a 60s solver budget. If a large move cap needs more
than that, every solve at the top of the slider fails and no prompt wording can
rescue it, so the real numbers decide whether the ceiling is right.
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import time
import urllib.error
import urllib.request


def _stack() -> dict[str, str]:
    state = pathlib.Path(f"deploy/state/stack.{getpass.getuser()}.env")
    values = {}
    for line in state.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


values = _stack()
ADAPTER = f"http://{values['STACK_NODE']}:{values.get('STACK_ADAPTER_PORT', '28002')}"
os.environ["WAREHOUSE_ENV_PRECEDENCE"] = "process"

from mocks.enterprise_services import MockServiceState, resolve_seed

state = MockServiceState(resolve_seed(7))
source = {
    "wms": state.snapshot(),
    "erp": {"sku_master": state.sku_master(), "inbound": state.inbound()},
    "forecast": state.forecast(14),
}

print(f"adapter: {ADAPTER}\n")
print(f"{'max_moves':>10} {'budget':>7} {'elapsed':>9}  outcome")
for max_moves, budget in ((30, 60), (60, 60), (120, 60), (120, 120), (120, 240)):
    body = {
        "goal": "probe",
        "planning_horizon_days": 7,
        "constraints": {"max_moves": max_moves, "cold_chain_locked": True, "locked_skus": []},
        "time_limit_s": budget,
        "source_data": source,
    }
    request = urllib.request.Request(
        f"{ADAPTER}/solve/slotting",
        data=json.dumps(body, default=str).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=budget + 120) as response:
            payload = json.load(response)
        moves = len(payload.get("moves") or [])
        print(f"{max_moves:>10} {budget:>7} {time.time() - started:>8.1f}s  {moves} moves")
    except urllib.error.HTTPError as exc:
        print(f"{max_moves:>10} {budget:>7} {time.time() - started:>8.1f}s  HTTP {exc.code}: {exc.read().decode()[:90]}")
    except Exception as exc:  # noqa: BLE001
        print(f"{max_moves:>10} {budget:>7} {time.time() - started:>8.1f}s  {type(exc).__name__}: {exc}")
