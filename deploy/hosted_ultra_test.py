#!/usr/bin/env python3
"""Probe a hosted Nemotron endpoint before anything is built on it.

Checks the model list, a plain completion, tool calling and streamed reasoning.
Credentials are read from .env, never hardcoded.

Usage: hosted_ultra_test.py [model-id]   (defaults to NIM_SUPERVISOR_MODEL)
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request


def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    file = pathlib.Path(path)
    if file.is_file():
        for line in file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                env[name.strip()] = value.strip()
    return env


ENV = load_env()
BASE = ENV.get("NIM_SUPERVISOR_BASE_URL", "").rstrip("/")
MODEL = sys.argv[1] if len(sys.argv) > 1 else ENV.get("NIM_SUPERVISOR_MODEL", "")
KEY = ENV.get("NIM_SUPERVISOR_API_KEY", "")

if not (BASE and MODEL and KEY):
    print("NIM_SUPERVISOR_* not configured in .env")
    raise SystemExit(1)

print(f"endpoint : {BASE}")
print(f"model    : {MODEL}")
print(f"api key  : set, {len(KEY)} chars\n")

HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def post(payload: dict, *, stream: bool = False, timeout: int = 180):
    req = urllib.request.Request(
        f"{BASE}/chat/completions", data=json.dumps(payload).encode(), headers=HEADERS
    )
    return urllib.request.urlopen(req, timeout=timeout)


def check(name: str, fn) -> bool:
    print(f"--- {name} ---")
    try:
        ok = fn()
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code}: {exc.read().decode()[:400]}\n")
        return False
    except Exception as exc:  # noqa: BLE001 - the point is to report any failure
        print(f"  {type(exc).__name__}: {exc}\n")
        return False
    print(f"  => {'PASS' if ok else 'FAIL'}\n")
    return ok


def list_models() -> bool:
    req = urllib.request.Request(f"{BASE}/models", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    ids = [m.get("id") for m in data.get("data", [])]
    print(f"  {len(ids)} model(s)")
    for i in ids[:15]:
        print(f"    {'* ' if i == MODEL else '  '}{i}")
    return MODEL in ids if ids else False


def completion() -> bool:
    start = time.time()
    with post({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        # These are reasoning models: the budget has to cover the reasoning
        # tokens as well, or the turn ends on length with empty content.
        "max_tokens": 2048,
    }) as resp:
        out = json.load(resp)
    msg = out["choices"][0]["message"]
    print(f"  content : {(msg.get('content') or '')[:120]!r}")
    print(f"  usage   : {out.get('usage')}")
    print(f"  latency : {time.time() - start:.2f}s")
    return bool(msg.get("content"))


def tool_calling() -> bool:
    tools = [{
        "type": "function",
        "function": {
            "name": "solve_slotting",
            "description": "Run the cuOpt constrained slotting solver.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_moves": {"type": "integer"},
                    "time_limit_s": {"type": "number"},
                },
                "required": ["max_moves", "time_limit_s"],
            },
        },
    }]
    with post({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You orchestrate a warehouse optimisation."},
            {"role": "user", "content": "Only 8.8% of class A demand is in the forward pick face. Run the solver; you choose the move cap and the time budget (max 60s)."},
        ],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 4096,
    }) as resp:
        out = json.load(resp)
    choice = out["choices"][0]
    calls = choice["message"].get("tool_calls") or []
    print(f"  finish_reason : {choice.get('finish_reason')}")
    for call in calls:
        print(f"  tool call     : {call['function']['name']}({call['function']['arguments']})")
    return bool(calls)


def streaming() -> bool:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "In two sentences, why put fast-moving SKUs near the dispatch dock?"}],
        "max_tokens": 4096,
        "stream": True,
    }
    reasoning_chars = answer_chars = chunks = 0
    keys: set[str] = set()
    first = None
    start = time.time()
    with post(payload, stream=True) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            delta = json.loads(body)["choices"][0].get("delta", {})
            keys.update(delta.keys())
            think = delta.get("reasoning") or delta.get("reasoning_content")
            piece = think or delta.get("content")
            if piece:
                chunks += 1
                first = first or time.time() - start
                if think:
                    reasoning_chars += len(piece)
                else:
                    answer_chars += len(piece)
    print(f"  delta keys seen : {sorted(keys)}")
    print(f"  chunks          : {chunks}  (first token {first:.2f}s)" if first else f"  chunks: {chunks}")
    print(f"  reasoning chars : {reasoning_chars}")
    print(f"  answer chars    : {answer_chars}")
    return chunks > 0


results = {
    "models": check("model list", list_models),
    "completion": check("plain completion", completion),
    "tool_calling": check("tool calling", tool_calling),
    "streaming": check("streaming + reasoning", streaming),
}
print("=== SUMMARY ===")
for name, ok in results.items():
    print(f"  {name:<14} {'PASS' if ok else 'FAIL'}")
sys.exit(0 if all(results.values()) else 1)
