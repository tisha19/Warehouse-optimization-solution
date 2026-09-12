#!/usr/bin/env python3
"""Hit the slotting adapter with a real payload and report the outcome.

Confirms whether the resubmit path recovers when cuOpt drops a queued job.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

PORT = os.getenv("ADAPTER_PORT", "28002")
URL = f"http://127.0.0.1:{PORT}/solve/slotting"

sys.path.insert(0, os.getcwd())
from mocks.enterprise_services import MockServiceState  # noqa: E402
from mocks.enterprise_adapters import (  # noqa: E402
    SyntheticERPAdapter,
    SyntheticForecastAdapter,
    SyntheticWMSAdapter,
)

state = MockServiceState(7)
erp = SyntheticERPAdapter(state)
source = {
    "wms": SyntheticWMSAdapter(state).snapshot(),
    "erp": {"sku_master": erp.sku_master(), "inbound": erp.inbound_shipments()},
    "forecast": SyntheticForecastAdapter(state).forecast(),
}
problem = {
    "goal": "Reduce picker travel over the next seven days.",
    "planning_horizon_days": 7,
    "constraints": {"max_moves": 30, "labour_minutes_per_window": 240},
    "objective": "minimise total pick distance",
    "time_limit_s": 30,
    "analysis": {},
    "source_data": source,
    "required_output": {
        "moves": ["sku_id", "from_slot", "to_slot", "day", "window", "reason",
                  "benefit_hours_per_day", "labor_minutes", "confidence"],
        "metrics": ["travel_reduction_pct", "replenishment_reduction_pct",
                    "constraint_violations", "plan_value"],
    },
}

rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
print(f"posting to {URL}", flush=True)
for attempt in range(1, rounds + 1):
    body = json.dumps(problem).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            payload = json.load(resp)
        moves = payload.get("moves") or payload.get("recommendations") or []
        print(f"attempt {attempt}: OK in {time.perf_counter() - started:.1f}s, {len(moves)} moves", flush=True)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        print(f"attempt {attempt}: HTTP {exc.code} after {time.perf_counter() - started:.1f}s :: {detail}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"attempt {attempt}: {type(exc).__name__} after {time.perf_counter() - started:.1f}s :: {exc}", flush=True)
