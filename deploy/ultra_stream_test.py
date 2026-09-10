#!/usr/bin/env python3
"""Stream a chat completion from the Ultra NIM, showing reasoning and answer live."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000/v1"
prompt = sys.argv[1] if len(sys.argv) > 1 else (
    "A distribution centre has 100 SKUs across 498 slots in six zones. Zone A is 6m "
    "from the dispatch dock, zone F is 74m. Only 8.8% of class A demand is picked "
    "from zone A. You may relocate at most 30 SKUs and each move costs about 8 "
    "operator-minutes. In three sentences, state the objective you would give a "
    "constrained solver and the single constraint most likely to bind."
)

model = json.load(urllib.request.urlopen(BASE + "/models", timeout=30))["data"][0]["id"]
print(f"model   : {model}")
print(f"prompt  : {prompt[:110]}...\n")

body = json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": 900,
    "temperature": 0.6,
    "stream": True,
}).encode()

req = urllib.request.Request(
    BASE + "/chat/completions", data=body, headers={"Content-Type": "application/json"}
)

start = time.time()
first = None
mode = None
tokens = 0
raw_shown = 0
with urllib.request.urlopen(req, timeout=300) as resp:
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        delta = json.loads(payload)["choices"][0].get("delta", {})
        think = delta.get("reasoning") or delta.get("reasoning_content")
        piece = think or delta.get("content")
        if not piece:
            if raw_shown < 3:
                print(f"[raw] {payload[:220]}")
                raw_shown += 1
            continue
        kind = "reasoning" if think else "answer"
        if kind != mode:
            print(f"\n\n--- {kind.upper()} ---")
            mode = kind
        if first is None:
            first = time.time() - start
        tokens += 1
        sys.stdout.write(piece)
        sys.stdout.flush()

total = time.time() - start
ttft = f"{first:.2f}s" if first is not None else "n/a"
print(f"\n\n--- first token {ttft} | {tokens} chunks | total {total:.2f}s "
      f"| {tokens / max(total, 0.01):.1f} chunks/s ---")
