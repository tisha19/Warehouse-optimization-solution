#!/usr/bin/env python3
"""Measure TTFT, generated tokens and total generation time for both Nemotron models.

Streams a representative warehouse planning prompt against the hosted endpoints
and reports per-trial numbers plus a median, so the deck can quote real figures.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


PROMPT = (
    "You are a warehouse slotting planner. Zone A (forward pick) is 54% full, "
    "zone B is 95% full, average pick travel is 74.9 m and forward-pick coverage "
    "is 8.8%. Explain which SKUs should move to the forward pick zone and why, "
    "then state the single biggest constraint on the plan."
)


def trial(base: str, model: str, key: str, max_tokens: int) -> dict[str, float]:
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode()

    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )

    started = time.perf_counter()
    ttft = None
    chunks = 0
    usage: dict[str, int] = {}

    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                # Reasoning models emit their thinking before any visible content,
                # so the first token of either kind is the real time to first token.
                if delta.get("content") or delta.get("reasoning_content"):
                    chunks += 1
                    if ttft is None:
                        ttft = time.perf_counter() - started

    total = time.perf_counter() - started
    completion = int(usage.get("completion_tokens") or 0)
    return {
        "ttft": ttft if ttft is not None else float("nan"),
        "total": total,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": completion,
        "chunks": chunks,
        "tok_per_s": completion / total if total > 0 and completion else float("nan"),
    }


def report(label: str, base: str, model: str, key: str, runs: int, max_tokens: int) -> None:
    print(f"\n=== {label} ===")
    print(f"model    : {model}")
    print(f"endpoint : {base}")
    results = []
    for i in range(1, runs + 1):
        try:
            r = trial(base, model, key, max_tokens)
        except Exception as exc:  # surfaced rather than hidden: a failed trial is a real result
            print(f"  run {i}: FAILED {type(exc).__name__}: {exc}")
            continue
        results.append(r)
        print(
            f"  run {i}: TTFT {r['ttft']:.2f}s | total {r['total']:.2f}s | "
            f"prompt {r['prompt_tokens']} tok | generated {r['completion_tokens']} tok | "
            f"{r['tok_per_s']:.1f} tok/s"
        )
    if not results:
        print("  no successful runs")
        return
    med = lambda k: statistics.median(r[k] for r in results)
    print(
        f"  MEDIAN: TTFT {med('ttft'):.2f}s | total {med('total'):.2f}s | "
        f"generated {med('completion_tokens'):.0f} tok | {med('tok_per_s'):.1f} tok/s"
    )


def main() -> int:
    env = load_env()
    runs = int(os.getenv("RUNS", "3"))
    max_tokens = int(os.getenv("MAX_TOKENS", "1024"))

    sup_base = env.get("NIM_SUPERVISOR_BASE_URL", "")
    sup_model = env.get("NIM_SUPERVISOR_MODEL", "")
    sup_key = env.get("NIM_SUPERVISOR_API_KEY", "")

    spec_base = env.get("NIM_SUBAGENT_BASE_URL") or sup_base
    spec_model = env.get("NIM_SUBAGENT_MODEL", "")
    spec_key = env.get("NIM_API_KEY") or sup_key

    if not (sup_base and sup_model and sup_key):
        print("supervisor endpoint/model/key missing from .env", file=sys.stderr)
        return 1

    report("Nemotron 3 Ultra (orchestrator)", sup_base, sup_model, sup_key, runs, max_tokens)
    if spec_model:
        report("Nemotron 3.5 Lightning (specialists)", spec_base, spec_model, spec_key, runs, max_tokens)
    else:
        print("\nno NIM_SUBAGENT_MODEL in .env - skipping specialists", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
