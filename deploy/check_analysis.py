#!/usr/bin/env python3
"""Run the slotting analysis once, directly, so failures are visible."""
from __future__ import annotations

import getpass
import os
import pathlib
import time


def _point_at_the_running_stack() -> None:
    state = pathlib.Path(f"deploy/state/stack.{getpass.getuser()}.env")
    values = {}
    for line in state.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    node = values["STACK_NODE"]
    os.environ["WAREHOUSE_ENV_PRECEDENCE"] = "process"
    os.environ["CUOPT_URL"] = f"http://{node}:{values.get('STACK_ADAPTER_PORT', '28002')}"
    os.environ["NEMO_GUARDRAILS_URL"] = f"http://{node}:{values.get('STACK_GUARDRAILS_PORT', '28003')}"
    os.environ["OPENSHELL_URL"] = f"http://{node}:{values.get('STACK_OPENSHELL_PORT', '28004')}"


_point_at_the_running_stack()

from agents.digest import warehouse_digest
from mocks.enterprise_services import MockServiceState, resolve_seed
from services.config import ProductionConfig
from services.harness_profile import ANALYSIS_MAX_TOKENS, specialist_model
from showcase.analysis import analyse_slotting
from showcase.kpis import slotting_headroom, warehouse_kpis

config = ProductionConfig.from_env()
state = MockServiceState(resolve_seed(20260911))
source = {
    "wms": state.snapshot(),
    "erp": {"sku_master": state.sku_master(), "inbound": state.inbound()},
    "forecast": state.forecast(14),
}
constraints = {"max_moves": 30, "locked_skus": [], "cold_chain_locked": True, "labour_minutes_per_window": 240}

kpis = warehouse_kpis(source)
headroom = slotting_headroom(source, constraints)
zones = [
    {"zone": z["zone"], "slots": z["slots"], "free": z["free"], "avg_distance_m": z["avg_distance_m"]}
    for z in warehouse_digest(source, constraints).get("zones", [])
]

print(f"model    : {config.nim_subagent_model}")
print(f"coverage : {kpis['forward_pick_coverage_pct']}%   travel/pick {kpis['avg_distance_per_pick_m']}m")
print(f"headroom : {headroom['headroom_metre_picks']} metre-picks ({headroom['headroom_pct']}%)\n")

started = time.time()
problems = analyse_slotting(
    specialist_model(config, max_tokens=ANALYSIS_MAX_TOKENS), kpis, headroom, constraints, zones
)
print(f"=== {len(problems)} finding(s) in {time.time() - started:.0f}s ===")
for problem in problems:
    print(f"\n[{problem['severity']}] addressable={problem['addressable']}  {problem['metric']}")
    print(f"  {problem['title']}")
    print(f"  {problem['detail']}")
