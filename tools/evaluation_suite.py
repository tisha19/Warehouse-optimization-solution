"""Standard evaluation entry point for all warehouse specialist agents."""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Sequence

from tools.evaluation import AgentCase, AgentEvaluator, write_evaluation_report


AGENT_NAMES = ("demand", "inventory", "warehouse", "orchestrator")


def evaluate_all_agents(runners: Mapping[str, Callable[[Dict[str, Any]], Dict[str, Any]]], cases: Sequence[AgentCase], report_path: str | None = None) -> Dict[str, Any]:
    """Run the same contract cases across every supplied agent runner."""
    missing = [name for name in AGENT_NAMES if name not in runners]
    if missing:
        raise ValueError("Runners are required for: " + ", ".join(missing))
    evaluator = AgentEvaluator()
    report = evaluator.evaluate_suite(dict(runners), {name: cases for name in AGENT_NAMES})
    if report_path:
        evaluator.trace.export(report_path.replace(".json", "_traces.json"))
        write_evaluation_report(report, report_path)
    return report
