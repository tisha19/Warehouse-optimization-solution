#!/usr/bin/env python3
"""Does the Ultra NIM support OpenAI-style tool calling? Deep Agents needs it."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/v1"
model = json.load(urllib.request.urlopen(BASE + "/models", timeout=30))["data"][0]["id"]
print(f"model: {model}\n")

tools = [{
    "type": "function",
    "function": {
        "name": "solve_slotting",
        "description": "Run the cuOpt constrained slotting solver over the warehouse.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_moves": {"type": "integer", "description": "Cap on relocations"},
                "time_limit_s": {"type": "number", "description": "Solver budget in seconds"},
            },
            "required": ["max_moves", "time_limit_s"],
        },
    },
}]

body = {
    "model": model,
    "messages": [
        {"role": "system", "content": "You orchestrate a warehouse slotting optimisation. Use the tools available."},
        {"role": "user", "content": "Only 8.8% of class A demand is in the forward pick face. Run the solver; you decide the move cap and how long it may run (max 60s)."},
    ],
    "tools": tools,
    "tool_choice": "auto",
    "max_tokens": 500,
}

req = urllib.request.Request(
    BASE + "/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        out = json.load(resp)
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}: {exc.read().decode()[:600]}")
    raise SystemExit(1)

msg = out["choices"][0]["message"]
calls = msg.get("tool_calls") or []
print(f"finish_reason : {out['choices'][0].get('finish_reason')}")
print(f"tool_calls    : {len(calls)}")
for call in calls:
    print(f"  -> {call['function']['name']}({call['function']['arguments']})")
if msg.get("reasoning"):
    print(f"\nreasoning (first 400):\n{msg['reasoning'][:400]}")
if msg.get("content"):
    print(f"\ncontent (first 300):\n{msg['content'][:300]}")
print(f"\nTOOL CALLING SUPPORTED: {bool(calls)}")
