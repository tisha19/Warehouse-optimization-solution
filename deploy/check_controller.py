#!/usr/bin/env python3
"""Drive the controller the way the UI does: start a run, poll, inspect the state."""
from __future__ import annotations

import getpass
import json
import os
import pathlib
import threading
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
    print(f"stack node: {node}")


_point_at_the_running_stack()

from services.config import ProductionConfig
from showcase.controller import ShowcaseController

config = ProductionConfig.from_env()


def auto_approve(stop: threading.Event) -> None:
    import urllib.request

    base = config.openshell_url.rstrip("/")
    while not stop.wait(2.0):
        try:
            with urllib.request.urlopen(f"{base}/api/v1/requests?status=pending", timeout=10) as response:
                pending = json.load(response)
        except Exception:  # noqa: BLE001
            continue
        for request in pending:
            body = json.dumps({"resolved_by": "test-admin", "reason": "controller check"}).encode()
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"{base}/api/v1/requests/{request['id']}/approve",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=10,
                ).read()
                print(f"  [admin] approved {request['service']}")
            except Exception:  # noqa: BLE001
                pass


stop = threading.Event()
threading.Thread(target=auto_approve, args=(stop,), daemon=True).start()

controller = ShowcaseController(seed=20260911)
print("dashboard ok:", bool(controller.dashboard().get("kpis")))

print("\n=== starting run ===")
controller.start_run()
deadline = time.time() + 900
last = None
while time.time() < deadline:
    run = controller.run()
    marker = (run["status"], len(run["delegations"]), len(run["rounds"]), run.get("chosen_round"))
    if marker != last:
        print(f"  status={run['status']} orchestrator={run['orchestrator']['status']} "
              f"delegations={len(run['delegations'])} rounds={len(run['rounds'])}")
        last = marker
    if run["status"] in ("COMPLETE", "FAILED"):
        break
    time.sleep(2)

run = controller.run()
stop.set()

print(f"\n=== final run state ===")
print(f"  status        : {run['status']}")
print(f"  error         : {run.get('error')}")
orchestrator = run["orchestrator"]
print(f"  orchestrator  : {orchestrator['status']} / {orchestrator['model']}")
print(f"  harness       : attached={orchestrator['harness'].get('attached')} "
      f"middleware={len(orchestrator['harness'].get('middleware') or [])} "
      f"suffix={orchestrator['harness'].get('prompt_suffix_chars')}")
print(f"  thinking lines: {len(orchestrator['thinking'])}")
print(f"  delegations   : {[(d['name'], d['status'], len(d['answer'])) for d in run['delegations']]}")
for record in run["rounds"]:
    print(f"  round {record['round']}: status={record['status']} max_moves={record['max_moves']} "
          f"moves={record.get('moves')} solver={record.get('solver_seconds')}s")
print(f"  chosen round  : {run.get('chosen_round')}")
print(f"  moves         : {len(run['moves'])}")
print(f"  approval      : {(run.get('approval') or {}).get('approval_id')}")
print(f"  guardrails    : {[(g['stage'], g['allowed']) for g in run['guardrails']]}")
print(f"  openshell     : {[(o['service'], o['allowed']) for o in run['openshell']]}")
print(f"  narrative     : {len(orchestrator['narrative'])} chars")

print("\n=== json-serialisable for the API? ===")
payload = json.dumps(run, default=str)
print(f"  ok, {len(payload)} bytes")

print("\n=== assertions ===")
failures = []
if run["status"] != "COMPLETE":
    failures.append(f"run did not complete: {run.get('error')}")
if not run["delegations"]:
    failures.append("no specialist delegations recorded")
if not run["rounds"]:
    failures.append("no solve rounds recorded")
if not run["moves"]:
    failures.append("no moves in the chosen round")
if not orchestrator["harness"].get("attached"):
    failures.append("harness not attached")
if not orchestrator["narrative"]:
    failures.append("no narrative")
if run.get("chosen_round") is None:
    failures.append("no chosen round recorded")
for failure in failures:
    print(f"  FAIL {failure}")
if not failures:
    print("  all assertions passed")
raise SystemExit(1 if failures else 0)
