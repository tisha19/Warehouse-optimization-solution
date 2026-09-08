"""Framework-agnostic evaluation and tracing for every warehouse agent."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence


@dataclass
class AgentCase:
    case_id: str
    input_data: Dict[str, Any]
    expected: Dict[str, Any]
    tags: List[str] = field(default_factory=list)


@dataclass
class AgentScore:
    agent: str
    case_id: str
    passed: bool
    quality_score: float
    latency_ms: float
    errors: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)


class TraceRecorder:
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def record(self, agent: str, event: str, **data: Any) -> None:
        self.events.append({"timestamp": datetime.now(timezone.utc).isoformat(), "agent": agent, "event": event, **data})

    def export(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.events, indent=2, default=str))


class AgentEvaluator:
    """Evaluate agents with deterministic checks plus optional custom scorers."""

    def __init__(self, trace: TraceRecorder | None = None):
        self.trace = trace or TraceRecorder()

    def evaluate(self, agent_name: str, runner: Callable[[Dict[str, Any]], Dict[str, Any]], cases: Sequence[AgentCase], scorer: Callable[[Dict[str, Any], Dict[str, Any]], Mapping[str, float]] | None = None) -> List[AgentScore]:
        scores: List[AgentScore] = []
        for case in cases:
            started = time.perf_counter()
            errors: List[str] = []
            output: Dict[str, Any] = {}
            self.trace.record(agent_name, "start", case_id=case.case_id)
            try:
                output = runner(case.input_data)
                metrics = dict(scorer(output, case.expected)) if scorer else self._default_score(output, case.expected)
            except Exception as exc:
                errors.append(str(exc))
                metrics = {"schema": 0.0, "expected_fields": 0.0, "constraint_compliance": 0.0}
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            quality = round(sum(metrics.values()) / max(len(metrics), 1) * 100, 2)
            passed = not errors and quality >= 80
            self.trace.record(agent_name, "complete", case_id=case.case_id, latency_ms=latency_ms, quality_score=quality, passed=passed, errors=errors)
            scores.append(AgentScore(agent_name, case.case_id, passed, quality, latency_ms, errors, metrics))
        return scores

    def evaluate_suite(self, agents: Mapping[str, Callable[[Dict[str, Any]], Dict[str, Any]]], cases_by_agent: Mapping[str, Sequence[AgentCase]]) -> Dict[str, Any]:
        results = {name: self.evaluate(name, runner, cases_by_agent.get(name, [])) for name, runner in agents.items()}
        return {"summary": self.summary(results), "scores": {name: [score.__dict__ for score in scores] for name, scores in results.items()}, "trace_events": len(self.trace.events)}

    @staticmethod
    def summary(results: Mapping[str, Sequence[AgentScore]]) -> Dict[str, Any]:
        all_scores = [score for scores in results.values() for score in scores]
        return {"agents": {name: {"cases": len(scores), "pass_rate_pct": round(sum(score.passed for score in scores) / max(len(scores), 1) * 100, 2), "mean_quality_score": round(sum(score.quality_score for score in scores) / max(len(scores), 1), 2), "mean_latency_ms": round(sum(score.latency_ms for score in scores) / max(len(scores), 1), 2)} for name, scores in results.items()}, "overall_pass_rate_pct": round(sum(score.passed for score in all_scores) / max(len(all_scores), 1) * 100, 2), "overall_quality_score": round(sum(score.quality_score for score in all_scores) / max(len(all_scores), 1), 2)}

    @staticmethod
    def _default_score(output: Mapping[str, Any], expected: Mapping[str, Any]) -> Dict[str, float]:
        errors = []
        if not isinstance(output, Mapping):
            errors.append("Output is not a mapping")
        expected_fields = [field for field in expected.get("required_fields", []) if field in output]
        schema = 1.0 if isinstance(output, Mapping) else 0.0
        field_score = len(expected_fields) / max(len(expected.get("required_fields", [])), 1)
        for key, value in expected.get("exact", {}).items():
            if output.get(key) != value:
                errors.append(f"Expected {key}={value!r}")
        compliance = 1.0 if not errors else 0.0
        return {"schema": schema, "expected_fields": field_score, "constraint_compliance": compliance}


def write_evaluation_report(report: Mapping[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, default=str))
