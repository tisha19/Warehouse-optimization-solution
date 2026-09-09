"""End-to-end check of the self-hosted stack from application code.

Runs the real LangGraph workflow against whatever endpoints .env points at and
prints the parts of the result that prove each dependency actually took part:
the cuOpt headline and moves, the NIM endpoint that answered, and the trace.

    ./.venv/bin/python -m tools.stack_check
"""

from __future__ import annotations

import json
import time

from agents.workflow import ProductionWarehouseWorkflow
from mocks.enterprise_adapters import SyntheticERPAdapter, SyntheticForecastAdapter, SyntheticWMSAdapter
from mocks.enterprise_services import MockServiceState
from services.config import ProductionConfig

GOAL = "Prepare a seven-day promotion plan. Keep moves below 10, lock cold-chain inventory, and prioritize picker travel."
CONSTRAINTS = {"max_moves": 10, "locked_skus": [], "cold_chain_locked": True, "execution_windows": ["low-volume shifts"]}


def main() -> int:
    config = ProductionConfig.from_env()
    print("== endpoints ==")
    print(f"  nim        {config.nim_base_url} ({config.nim_model})")
    print(f"  subagent   {config.nim_subagent_base_url} ({config.nim_subagent_model})")
    print(f"  cuopt      {config.cuopt_url}")
    print(f"  guardrails {config.guardrails_url}")
    print(f"  openshell  {config.openshell_url}")

    # WMS/ERP/forecast are still synthetic, so feed the workflow the same
    # adapters the UI uses instead of the unconfigured HTTP endpoints.
    enterprise = MockServiceState(7)
    workflow = ProductionWarehouseWorkflow(
        config=config,
        wms=SyntheticWMSAdapter(enterprise),
        erp=SyntheticERPAdapter(enterprise),
        forecast=SyntheticForecastAdapter(enterprise),
    )
    started = time.perf_counter()
    state = workflow.run(GOAL, "hackathon-planner", dict(CONSTRAINTS))
    elapsed = time.perf_counter() - started

    solution = state.get("solution") or {}
    moves = solution.get("moves") or solution.get("recommendations") or []
    print("\n== result ==")
    print(f"  headline   {solution.get('headline')}")
    print(f"  moves      {len(moves)}")
    print(f"  metrics    {json.dumps(solution.get('metrics') or {}, sort_keys=True)}")
    print(f"  supervisor {workflow.nim.active_endpoint}")
    print(f"  subagent   {workflow.subagent_nim.active_endpoint}")
    print(f"  validation {json.dumps(state.get('validation') or {}, sort_keys=True)}")
    print(f"  trace      {[node.get('node') for node in state.get('trace') or []]}")
    print(f"  approval   {(state.get('approval') or {}).get('id')}")
    print(f"  elapsed    {elapsed:.1f}s")

    if "cuOpt" not in str(solution.get("headline") or ""):
        print("\nFAIL: the plan did not come from cuOpt")
        return 1
    print("\nPASS: the plan came from cuOpt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
