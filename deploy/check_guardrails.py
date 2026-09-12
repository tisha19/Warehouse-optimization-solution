#!/usr/bin/env python3
"""Send the run's input payload through Guardrails and print the verdict.

The rails are an LLM self-check, so a block is worth reproducing rather than
guessing at.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.getcwd())
from services.config import ProductionConfig  # noqa: E402
from services.nvidia import GuardrailsClient  # noqa: E402

config = ProductionConfig.from_env()
client = GuardrailsClient(config)
print(f"url       : {config.guardrails_url}")
print(f"config_id : {config.guardrails_config_id}")
print(f"model     : {config.nim_model}\n")

goal = (
    "Reduce picker travel over the next seven days. Stay within the move cap, "
    "keep cold-chain stock where it is, and prioritise the highest-demand lines."
)
payload = {
    "goal": goal,
    "constraints": {
        "max_moves": 30,
        "locked_skus": [],
        "cold_chain_locked": True,
        "labour_minutes_per_window": 240,
        "execution_windows": ["low-volume shifts"],
    },
}

runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
blocked = 0
for attempt in range(1, runs + 1):
    decision = client.validate("input", payload)
    if not decision.allowed:
        blocked += 1
    print(f"attempt {attempt}: allowed={decision.allowed}  {decision.reason[:160]}")

print(f"\n{blocked} of {runs} blocked")
if blocked:
    print("\nraw response for one call:")
    print(json.dumps(client.client.post("v1/guardrail/checks", client._request("input", payload)), indent=1)[:1200])
