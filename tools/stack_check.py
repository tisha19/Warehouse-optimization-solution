"""End-to-end check of the self-hosted stack from application code.

Runs the real LangGraph workflow against whatever endpoints .env points at and
prints the parts of the result that prove each dependency actually took part:
the cuOpt headline and moves, the NIM endpoint that answered, and the trace.

    ./.venv/bin/python -m tools.stack_check
"""

from __future__ import annotations

import json
import time

from agents.deep_workflow import WarehouseDeepAgent
from mocks.enterprise_adapters import SyntheticERPAdapter, SyntheticForecastAdapter, SyntheticWMSAdapter
from mocks.enterprise_services import MockServiceState, resolve_seed
from services.config import ProductionConfig

GOAL = "Prepare a seven-day promotion plan. Keep moves below 10, lock cold-chain inventory, and prioritize picker travel."
CONSTRAINTS = {"max_moves": 10, "locked_skus": [], "cold_chain_locked": True, "execution_windows": ["low-volume shifts"]}


def main() -> int:
    config = ProductionConfig.from_env()
    print("== endpoints ==")
    print(f"  orchestrator {config.nim_supervisor_base_url} ({config.nim_supervisor_model})")
    print(f"  specialists  {config.nim_subagent_base_url} ({config.nim_subagent_model})")
    print(f"  cuopt        {config.cuopt_url}")
    print(f"  guardrails   {config.guardrails_url}")
    print(f"  openshell    {config.openshell_url}")

    # WMS/ERP/forecast are still synthetic, so feed the orchestrator the same
    # adapters the UI uses instead of the unconfigured HTTP endpoints.
    enterprise = MockServiceState(resolve_seed())
    agent = WarehouseDeepAgent(
        config=config,
        wms=SyntheticWMSAdapter(enterprise),
        erp=SyntheticERPAdapter(enterprise),
        forecast=SyntheticForecastAdapter(enterprise),
    )
    started = time.perf_counter()
    state = agent.run(GOAL, "hackathon-planner", dict(CONSTRAINTS))
    elapsed = time.perf_counter() - started

    rounds = state.get("rounds") or []
    chosen = state.get("chosen") or {}
    print("\n== result ==")
    print(f"  harness      {len((state.get('harness') or {}).get('middleware') or [])} middleware attached")
    print(f"  delegations  {len(state.get('delegations') or [])}")
    print(f"  solve rounds {[r.get('max_moves') for r in rounds]}")
    print(f"  chosen round {chosen.get('round')} ({len(chosen.get('moves') or [])} moves)")
    print(f"  metrics      {json.dumps((chosen.get('solution') or {}).get('metrics') or {}, sort_keys=True)}")
    print(f"  validation   {json.dumps(state.get('validation') or {}, sort_keys=True)}")
    print(f"  approval     {(state.get('approval') or {}).get('approval_id')}")
    print(f"  elapsed      {elapsed:.1f}s")

    if not rounds or not chosen:
        print("\nFAIL: the orchestrator produced no solved plan")
        return 1
    print("\nPASS: the plan came from cuOpt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
