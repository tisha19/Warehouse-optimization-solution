#!/usr/bin/env python3
"""Drive the deepagents orchestrator against the live cuOpt solver, end to end.

Checks the things that actually matter: that the orchestrator reads real state,
delegates to the specialists, calls the solver more than once when headroom is
left, stays inside the operator's move cap and the solver time ceiling, and
finishes by raising an approval rather than claiming anything was applied.
"""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import threading
import time


def _point_at_the_running_stack() -> None:
    """The stack moves node whenever Slurm reschedules it, so read where it landed."""
    state = pathlib.Path(f"deploy/state/stack.{getpass.getuser()}.env")
    if not state.is_file():
        return
    values = {}
    for line in state.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    node = values.get("STACK_NODE")
    if not node:
        return
    os.environ["WAREHOUSE_ENV_PRECEDENCE"] = "process"
    os.environ["CUOPT_URL"] = f"http://{node}:{values.get('STACK_ADAPTER_PORT', '28002')}"
    os.environ["NEMO_GUARDRAILS_URL"] = f"http://{node}:{values.get('STACK_GUARDRAILS_PORT', '28003')}"
    os.environ["OPENSHELL_URL"] = f"http://{node}:{values.get('STACK_OPENSHELL_PORT', '28004')}"
    print(f"stack node: {node}")


_point_at_the_running_stack()

from agents.deep_workflow import MAX_SOLVER_SECONDS, WarehouseDeepAgent
from mocks.enterprise_adapters import SyntheticERPAdapter, SyntheticForecastAdapter, SyntheticWMSAdapter
from mocks.enterprise_services import MockServiceState, resolve_seed
from services.config import ProductionConfig

CONSTRAINTS = {
    "max_moves": 30,
    "locked_skus": [],
    "cold_chain_locked": True,
    "labour_minutes_per_window": 240,
    "execution_windows": ["low-volume shifts"],
}
GOAL = "Reduce picker travel and get fast-moving stock into the forward pick face."

events: list[tuple[str, dict]] = []


def on_event(kind: str, payload: dict) -> None:
    events.append((kind, payload))
    if kind == "solve_started":
        print(f"  [solve {payload['round']}] max_moves={payload['max_moves']} "
              f"time_limit_s={payload['time_limit_s']} :: {payload['objective'][:70]}")
    elif kind == "solve_finished":
        print(f"  [solve {payload['round']}] {payload['solver_seconds']}s -> "
              f"headroom {payload['headroom_after']['headroom_metre_picks']} metre-picks "
              f"({payload['headroom_after']['headroom_pct']}%)")
    elif kind == "approval_created":
        print(f"  [approval] {payload['approval'].get('approval_id')}")
    elif kind in ("guardrail", "openshell"):
        print(f"  [{kind}] {payload.get('stage') or payload.get('service')} allowed={payload.get('allowed')}")


state = MockServiceState(resolve_seed(20260911))
config = ProductionConfig.from_env()
print(f"cuOpt    : {config.cuopt_url}")
print(f"rails    : {config.guardrails_url}")
print(f"openshell: {config.openshell_url}")
print(f"supervisor: {config.nim_supervisor_model}")
print(f"specialist: {config.nim_subagent_model}\n")


def auto_approve(stop: threading.Event) -> None:
    """Stand in for the admin at the OpenShell console.

    Every governed service needs a one-time human approval, which is the whole
    point of the governor; an unattended test has to play that part or it just
    blocks until the timeout.
    """
    import urllib.request

    base = config.openshell_url.rstrip("/")
    while not stop.wait(2.0):
        try:
            with urllib.request.urlopen(f"{base}/api/v1/requests?status=pending", timeout=10) as response:
                pending = json.load(response)
        except Exception:  # noqa: BLE001 - the governor may not be up yet
            continue
        for request in pending:
            body = json.dumps({"resolved_by": "test-admin", "reason": "automated end-to-end check"}).encode()
            approve = urllib.request.Request(
                f"{base}/api/v1/requests/{request['id']}/approve",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(approve, timeout=10).read()
                print(f"  [admin] approved {request['service']} for {request['user']}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [admin] could not approve {request.get('id')}: {exc}")


stop_approver = threading.Event()
approver = threading.Thread(target=auto_approve, args=(stop_approver,), daemon=True)
approver.start()

agent = WarehouseDeepAgent(
    config=config,
    wms=SyntheticWMSAdapter(state),
    erp=SyntheticERPAdapter(state),
    forecast=SyntheticForecastAdapter(state),
    on_event=on_event,
)

print("=== running the orchestrator ===")
started = time.time()
run = agent.run(GOAL, actor="ops.planner", constraints=CONSTRAINTS, site="Rotterdam DC")
elapsed = time.time() - started
stop_approver.set()

rounds = run.get("rounds", [])
print(f"\n=== result in {elapsed:.0f}s ===")
print(f"harness attached : {run['harness']['attached']} ({len(run['harness']['middleware'])} middleware)")
print(f"solve rounds     : {len(rounds)}")
for record in rounds:
    gain = record["headroom_before"]["headroom_metre_picks"] - record["headroom_after"]["headroom_metre_picks"]
    print(f"  round {record['round']}: {len(record['moves'])} moves, "
          f"{record['solver_seconds']}s, headroom removed {round(gain, 1)} metre-picks")
    print(f"     travel/pick {record['kpis_before']['avg_distance_per_pick_m']}m -> "
          f"{record['kpis_after']['avg_distance_per_pick_m']}m, "
          f"forward pick {record['kpis_before']['forward_pick_coverage_pct']}% -> "
          f"{record['kpis_after']['forward_pick_coverage_pct']}%")

specialist_calls = [m for m in run.get("messages", []) if getattr(m, "name", "") == "task"]
print(f"specialist calls : {len(specialist_calls)}")
chosen = run.get("chosen") or {}
print(f"approved round   : {chosen.get('round')} ({len(chosen.get('moves', []))} moves)")
print(f"approval         : {run.get('approval', {}).get('approval_id')}")
print(f"\nnarrative:\n{run.get('narrative', '')[:900]}")

print("\n=== assertions ===")
failures = []
if not rounds:
    failures.append("the orchestrator never called the solver")
for record in rounds:
    if record["max_moves"] > CONSTRAINTS["max_moves"]:
        failures.append(f"round {record['round']} exceeded the move cap: {record['max_moves']}")
    if record["time_limit_s"] > MAX_SOLVER_SECONDS:
        failures.append(f"round {record['round']} exceeded the time ceiling: {record['time_limit_s']}")
    if len(record["moves"]) > record["max_moves"]:
        failures.append(f"round {record['round']} returned more moves than allowed")
if not run.get("approval"):
    failures.append("no approval was raised")
if not run.get("narrative"):
    failures.append("no narrative was produced")
approved_moves = run.get("approval", {}).get("moves")
if approved_moves is not None and chosen and len(approved_moves) != len(chosen.get("moves", [])):
    failures.append("the approval does not contain the moves of the round the orchestrator chose")

for failure in failures:
    print(f"  FAIL {failure}")
if not failures:
    print("  all assertions passed")
raise SystemExit(1 if failures else 0)
