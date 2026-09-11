#!/usr/bin/env python3
"""Prove the Nemotron harness is attached and that a real agent uses a real tool.

This is the regression guard for the silent failure: if deepagents ever changes
the profile module, or the hosted model id changes, `attached` goes False and the
whole harness stops applying without any error.
"""
from __future__ import annotations

from deepagents import create_deep_agent
from langchain_core.tools import tool

from services.harness_profile import (
    MAX_REPEATED_TOOL_CALLS,
    profile_report,
    specialist_model,
    supervisor_model,
)

calls: list[dict] = []


@tool
def solve_slotting(max_moves: int, time_limit_s: float) -> str:
    """Run the cuOpt constrained slotting solver and return the resulting metrics."""
    calls.append({"max_moves": max_moves, "time_limit_s": time_limit_s})
    return (
        '{"moves_applied": 18, "travel_reduction_pct": 6.4, '
        '"remaining_addressable_issues": 0, "constraint_violations": 0}'
    )


print("=== supervisor ===")
supervisor = supervisor_model()
print(f"  model: {supervisor.model}")
report = profile_report(supervisor)
print(f"  harness attached   : {report['attached']}")
print(f"  middleware         : {len(report['middleware'])}")
for name in report["middleware"]:
    print(f"      {name}")
print(f"  prompt suffix chars: {report['prompt_suffix_chars']}")

print("\n=== progress budget actually in effect ===")
from deepagents.profiles.harness import harness_profiles as hp

hp._ensure_harness_profiles_loaded()
profile = hp._harness_profile_for_model(supervisor, None)
for item in hp._resolve_middleware_seq(profile.extra_middleware):
    if "ProgressBudget" in type(item).__name__:
        print(f"  max_model_calls        = {item.max_model_calls}")
        print(f"  max_tool_results       = {item.max_tool_results}")
        print(f"  max_repeated_tool_calls= {item.max_repeated_tool_calls}")
        assert item.max_repeated_tool_calls == MAX_REPEATED_TOOL_CALLS, "budget override did not apply"

print("\n=== specialist ===")
specialist = specialist_model()
print(f"  model: {specialist.model}")

print("\n=== live agent run (hosted Ultra + real tool call) ===")
agent = create_deep_agent(
    model=supervisor,
    tools=[solve_slotting],
    system_prompt=(
        "You are the warehouse optimisation orchestrator. "
        "Use the solve_slotting tool to fix slotting problems, then report the outcome in one sentence. "
        "The warehouse has 100 SKUs and a move cap of 30, so never request more moves than that."
    ),
)
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Only 8.8% of class A demand sits in the forward pick face. Fix it."}]},
    {"recursion_limit": 60},
)
final = result["messages"][-1]
print(f"  tool calls made : {calls}")
print(f"  final message   : {(final.content or '')[:300]!r}")

assert calls, "the agent never called the tool"
assert calls[0]["max_moves"] <= 30, f"agent ignored the move cap: {calls[0]}"
print("\nALL CHECKS PASSED")
