"""Weighted executive evaluation for DeepAgent warehouse runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping

WEIGHTS = {
    "business_impact": 30,
    "task_success": 20,
    "planning_quality": 15,
    "autonomy": 15,
    "efficiency": 10,
    "reliability": 10,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _summary_metrics(run: Mapping[str, Any]) -> Dict[str, Any]:
    summary = run.get("summary") or {}
    metrics = summary.get("metrics") or {}
    return dict(metrics) if isinstance(metrics, Mapping) else {}


def _rounds(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rounds = run.get("rounds") or []
    return [round_ for round_ in rounds if isinstance(round_, Mapping)]


def _failed_rounds(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [round_ for round_ in _rounds(run) if round_.get("status") == "failed"]


def _chosen_round(run: Mapping[str, Any]) -> Mapping[str, Any] | None:
    chosen = run.get("chosen_round")
    if chosen is None:
        return None
    for round_ in _rounds(run):
        if round_.get("round") == chosen:
            return round_
    return None


def _best_benefit(rounds: Iterable[Mapping[str, Any]]) -> float:
    best = 0.0
    for round_ in rounds:
        before = (((round_.get("headroom_before") or {}).get("headroom_metre_picks")) if isinstance(round_.get("headroom_before"), Mapping) else None)
        after = (((round_.get("headroom_after") or {}).get("headroom_metre_picks")) if isinstance(round_.get("headroom_after"), Mapping) else None)
        if before is None or after is None:
            continue
        best = max(best, max(0.0, float(before) - float(after)))
    return best


def _chosen_benefit(round_: Mapping[str, Any] | None) -> float:
    if not round_:
        return 0.0
    before = round_.get("headroom_before") or {}
    after = round_.get("headroom_after") or {}
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return 0.0
    before_value = _number(before.get("headroom_metre_picks"))
    after_value = _number(after.get("headroom_metre_picks"))
    if before_value is None or after_value is None:
        return 0.0
    return max(0.0, before_value - after_value)


def _metric(label: str, value: Any, unit: str, score: float, note: str = "") -> Dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "score": round(score, 2),
        "note": note,
    }


def _percent_score(value: float | None) -> float:
    return 50.0 if value is None else _clamp(value)


def _inverse_percent_score(value: float | None) -> float:
    return 50.0 if value is None else _clamp(100.0 - value)


def _distance_delta(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None, key: str) -> float | None:
    if not before or not after:
        return None
    before_value = _number(before.get(key))
    after_value = _number(after.get(key))
    if before_value is None or after_value is None or before_value <= 0:
        return None
    return _clamp((before_value - after_value) / before_value * 100.0)


def _latency_score(duration_ms: float | None) -> float:
    if duration_ms is None:
        return 50.0
    if duration_ms <= 180000.0:
        return _clamp(100.0 - duration_ms / 18000.0)
    return _clamp(90.0 - (duration_ms - 180000.0) / 12000.0)


def _token_score(tokens: float | None) -> float:
    if tokens is None:
        return 50.0
    if tokens <= 250000.0:
        return _clamp(100.0 - tokens / 25000.0)
    return _clamp(90.0 - (tokens - 250000.0) / 50000.0)


def _utilization_score(value: float | None, target: float = 85.0) -> float:
    if value is None:
        return 50.0
    return _clamp(100.0 - abs(value - target) * 1.25)


def _round_metric_value(value: float | None) -> float | None:
    return _round(value)


def _derived_sla_adherence(
    run_complete: bool,
    failed_rounds: list[Mapping[str, Any]],
    constraint_violations: float | None,
) -> float:
    if not run_complete:
        return _clamp(70.0 - min(len(failed_rounds), 3) * 10.0)
    base = 97.0
    base -= min(_number(constraint_violations) or 0.0, 4.0) * 12.0
    base -= min(len(failed_rounds), 3) * 4.0
    return _clamp(base)


def _derived_inventory_accuracy(
    run_complete: bool,
    chosen_round: Mapping[str, Any] | None,
    validation: Mapping[str, Any] | None,
) -> float:
    kpis_after = (chosen_round or {}).get("kpis_after") if isinstance(chosen_round, Mapping) else None
    if isinstance(kpis_after, Mapping):
        forward_pick = _number(kpis_after.get("forward_pick_coverage_pct"))
        if forward_pick is not None:
            return _clamp(forward_pick + (2.0 if run_complete else 0.0))
    if isinstance(validation, Mapping) and validation.get("status") == "PASSED":
        return 92.0 if run_complete else 75.0
    return 78.0 if run_complete else 65.0


def build_scorecard(run: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a weighted executive scorecard from a DeepAgent run snapshot."""

    run_complete = run.get("status") == "COMPLETE"
    summary_metrics = _summary_metrics(run)
    rounds = _rounds(run)
    chosen = _chosen_round(run)
    failed_rounds = _failed_rounds(run)
    best_benefit = _best_benefit(rounds)
    chosen_benefit = _chosen_benefit(chosen)
    move_count = len(run.get("moves") or [])
    max_moves = _number((run.get("constraints") or {}).get("max_moves")) or 0.0
    labour_budget = _number((run.get("constraints") or {}).get("labour_minutes_per_window")) or 0.0
    total_labor_minutes = sum(_number(move.get("labor_minutes")) or 0.0 for move in (run.get("moves") or []))
    duration_ms = _number(((run.get("orchestrator") or {}).get("duration_ms")))
    usage = run.get("usage") or {}
    if isinstance(usage, Mapping):
        tokens = (_number(usage.get("prompt_tokens")) or 0.0) + (_number(usage.get("completion_tokens")) or 0.0)
    else:
        tokens = 0.0

    business_travel = _distance_delta(
        (chosen or {}).get("kpis_before") if isinstance(chosen, Mapping) else None,
        (chosen or {}).get("kpis_after") if isinstance(chosen, Mapping) else None,
        "avg_distance_per_pick_m",
    )
    throughput = _number(summary_metrics.get("travel_reduction_pct"))
    if throughput is None:
        throughput = business_travel
    cost_reduction = _number(summary_metrics.get("cost_reduction_pct"))
    if cost_reduction is None:
        labour_utilization = (
            _clamp(total_labor_minutes / max(labour_budget, 1.0) * 100.0)
            if labour_budget
            else _clamp(total_labor_minutes / max(move_count * 15.0, 1.0) * 100.0) if move_count else None
        )
        cost_reduction = _clamp(
            (throughput or 0.0) * 0.7 + (100.0 - (labour_utilization or 0.0)) * 0.3
        ) if throughput is not None or labour_utilization is not None else 0.0
    resource_utilization = _number(summary_metrics.get("resource_utilization_pct"))
    if resource_utilization is None:
        if labour_budget:
            resource_utilization = _clamp(total_labor_minutes / labour_budget * 100.0)
        elif max_moves:
            resource_utilization = _clamp((total_labor_minutes or move_count * 15.0) / max(max_moves * 15.0, 1.0) * 100.0)
        else:
            resource_utilization = _clamp(total_labor_minutes / max(move_count * 15.0, 1.0) * 100.0) if move_count else 0.0
    if run_complete and move_count > 0 and resource_utilization == 0.0:
        resource_utilization = 1.0
    cycle_time_reduction = _number(summary_metrics.get("cycle_time_reduction_pct"))
    if cycle_time_reduction is None:
        cycle_time_reduction = business_travel
    sla_adherence = _number(summary_metrics.get("sla_adherence_pct"))
    if sla_adherence is None:
        sla_adherence = _derived_sla_adherence(run_complete, failed_rounds, _number(summary_metrics.get("constraint_violations")))
    revenue_uplift = _number(summary_metrics.get("revenue_uplift"))
    if revenue_uplift is None:
        revenue_uplift = _clamp((throughput or 0.0) * max(move_count, 1) / 5.0)
    inventory_accuracy = _number(summary_metrics.get("inventory_accuracy"))
    if inventory_accuracy is None:
        inventory_accuracy = _derived_inventory_accuracy(run_complete, chosen, run.get("validation"))
    customer_satisfaction = _number(summary_metrics.get("customer_satisfaction"))
    if customer_satisfaction is None:
        customer_satisfaction = _clamp((sla_adherence + inventory_accuracy) / 2.0) if run_complete else 0.0

    completion_rate = 100.0 if run_complete else 0.0
    goal_achievement_rate = 100.0 if run_complete and move_count > 0 and run.get("summary") else 0.0
    first_time_success_rate = (
        100.0
        if run_complete and len(rounds) <= 1 and not failed_rounds
        else _clamp(100.0 - max(0, len(rounds) - 1) * 20.0) if run_complete else 0.0
    )
    plan_execution_accuracy = _clamp(100.0 - (_number(summary_metrics.get("constraint_violations")) or 0.0) * 25.0) if run_complete else 0.0

    plan_quality = _number(summary_metrics.get("plan_value"))
    if plan_quality is None:
        plan_quality = _clamp((chosen_benefit or 0.0) * 5.0) if run_complete else 0.0
    replans_required = max(0, len(rounds) - 1)
    replanning_score = _clamp(100.0 - replans_required * 20.0) if run_complete else 0.0
    decision_optimality = 0.0 if not run_complete or best_benefit <= 0 else _clamp(chosen_benefit / best_benefit * 100.0)
    constraint_satisfaction = _clamp(100.0 - (_number(summary_metrics.get("constraint_violations")) or 0.0) * 25.0) if run_complete else 0.0
    root_cause = _number(summary_metrics.get("root_cause_identification_accuracy"))
    if root_cause is None:
        root_cause = 90.0 if run_complete and ((run.get("orchestrator") or {}).get("thinking") or (run.get("summary") or {}).get("explanation")) else 0.0

    halted = 1 if run.get("status") == "HALTED" else 0
    openshell_events = len(run.get("openshell") or [])
    human_intervention_rate = 100.0 if not run_complete else _clamp(halted / max(openshell_events, 1) * 100.0)
    autonomous_completion = 100.0 if run_complete and not run.get("halted_on") else 0.0
    decision_confidence = decision_optimality if run_complete else 0.0
    escalation_rate = 100.0 if not run_complete else _clamp(halted / max(openshell_events, 1) * 100.0)

    latency = duration_ms if duration_ms is not None else max((_number(round_.get("solver_seconds")) or 0.0) * 1000.0 for round_ in rounds) if rounds else None
    response_time = latency
    compute_cost_score = _clamp((_latency_score(latency) + _token_score(tokens)) / 2.0)
    resource_utilization_score = _utilization_score(resource_utilization)

    failed_execution_rate = 100.0 if run.get("status") == "FAILED" and not rounds else _clamp(len(failed_rounds) / max(len(rounds), 1) * 100.0)
    recovery_rate = 100.0 if run_complete and (not failed_rounds or len(rounds) > len(failed_rounds)) else (0.0 if run.get("status") == "FAILED" else 75.0)
    hallucination_risk = 100.0 if run.get("status") == "FAILED" else 40.0 if not (run.get("summary") or {}).get("explanation") else 0.0
    exception_handling = 100.0 if run_complete and not run.get("error") else 0.0 if run.get("status") == "FAILED" else 80.0
    resilience_to_missing_data = 100.0 if run_complete and run.get("summary") else 0.0 if run.get("status") == "FAILED" else 70.0

    categories = [
        {
            "key": "business_impact",
            "label": "Business Impact",
            "weight": WEIGHTS["business_impact"],
            "metrics": [
                _metric("Throughput improvement", _round_metric_value(throughput), "%", _percent_score(throughput or 0.0), "Travel reduction and picks/hour uplift"),
                _metric("Cost reduction", _round_metric_value(cost_reduction), "%", _percent_score(cost_reduction or 0.0), "Labour and travel savings mix"),
                _metric("Resource utilization", _round_metric_value(resource_utilization), "%", _utilization_score(resource_utilization), "Window utilization and labor fit"),
                _metric("Cycle time reduction", _round_metric_value(cycle_time_reduction), "%", _percent_score(cycle_time_reduction or 0.0), "Per-pick travel reduction"),
                _metric("SLA adherence", _round_metric_value(sla_adherence), "%", _percent_score(sla_adherence), "Plan stayed within operating rules"),
                _metric("Revenue uplift", _round_metric_value(revenue_uplift), "%", _percent_score(revenue_uplift or 0.0), "Demand flow preserved"),
                _metric("Inventory accuracy", _round_metric_value(inventory_accuracy), "%", _percent_score(inventory_accuracy), "Forward-pick and location quality"),
                _metric("Customer satisfaction", _round_metric_value(customer_satisfaction), "%", _percent_score(customer_satisfaction), "Service continuity indicator"),
            ],
        },
        {
            "key": "task_success",
            "label": "Task Success",
            "weight": WEIGHTS["task_success"],
            "metrics": [
                _metric("Task completion rate", completion_rate, "%", _percent_score(completion_rate), "Did the run finish"),
                _metric("Goal achievement rate", goal_achievement_rate, "%", _percent_score(goal_achievement_rate), "Produced a usable move plan"),
                _metric("First-time success rate", _round(first_time_success_rate), "%", _percent_score(first_time_success_rate), "Completed without rework"),
                _metric("Plan execution accuracy", _round(plan_execution_accuracy), "%", _percent_score(plan_execution_accuracy), "Constraint-compliant output"),
            ],
        },
        {
            "key": "planning_quality",
            "label": "Planning Quality",
            "weight": WEIGHTS["planning_quality"],
            "metrics": [
                _metric("Planning score", _round(plan_quality), "%", _percent_score(plan_quality), "Overall quality of the chosen plan"),
                _metric("Replans required", replans_required, "runs", replanning_score, "Fewer rounds is better"),
                _metric("Decision optimality", _round(decision_optimality), "%", _percent_score(decision_optimality), "Chosen round vs best round"),
                _metric("Constraint satisfaction", _round(constraint_satisfaction), "%", _percent_score(constraint_satisfaction), "Violation-free execution"),
                _metric("Root cause identification", _round(root_cause), "%", _percent_score(root_cause), "Did the agent isolate the bottleneck"),
            ],
        },
        {
            "key": "autonomy",
            "label": "Autonomy",
            "weight": WEIGHTS["autonomy"],
            "metrics": [
                _metric("Human intervention rate", _round(human_intervention_rate), "%", _inverse_percent_score(human_intervention_rate), "Lower is better"),
                _metric("Autonomous completion", autonomous_completion, "%", _percent_score(autonomous_completion), "Finished without blocking"),
                _metric("Decision confidence", _round(decision_confidence), "%", _percent_score(decision_confidence), "Chosen round certainty"),
                _metric("Escalation rate", _round(escalation_rate), "%", _inverse_percent_score(escalation_rate), "Lower is better"),
            ],
        },
        {
            "key": "efficiency",
            "label": "Efficiency",
            "weight": WEIGHTS["efficiency"],
            "metrics": [
                _metric("Latency", _round(latency, 0), "ms", _latency_score(latency), "Time to reach a decision"),
                _metric("Token consumption", _round(tokens, 0), "tokens", _token_score(tokens), "LLM usage cost"),
                _metric("Compute cost", _round(duration_ms if duration_ms is not None else tokens, 0), "weighted", compute_cost_score, "Latency and token blend"),
                _metric("Response time", _round(response_time, 0), "ms", _latency_score(response_time), "End-to-end runtime"),
            ],
        },
        {
            "key": "reliability",
            "label": "Reliability",
            "weight": WEIGHTS["reliability"],
            "metrics": [
                _metric("Failed executions", _round(failed_execution_rate), "%", _inverse_percent_score(failed_execution_rate), "Lower is better"),
                _metric("Recovery rate", _round(recovery_rate), "%", _percent_score(recovery_rate), "Recovered after solver or tool failures"),
                _metric("Hallucination rate", _round(hallucination_risk), "%", _inverse_percent_score(hallucination_risk), "Lower is better"),
                _metric("Exception handling", _round(exception_handling), "%", _percent_score(exception_handling), "Handled runtime errors cleanly"),
                _metric("Missing-data resilience", _round(resilience_to_missing_data), "%", _percent_score(resilience_to_missing_data), "Stayed useful when data was thin"),
            ],
        },
    ]

    for category in categories:
        scores = [metric["score"] for metric in category["metrics"]]
        category["score"] = round(sum(scores) / max(len(scores), 1), 2)

    overall_score = round(
        sum(category["score"] * category["weight"] for category in categories) / sum(WEIGHTS.values()),
        2,
    )
    strongest = max(categories, key=lambda item: item["score"])
    weakest = min(categories, key=lambda item: item["score"])
    summary = (
        f"Strongest category: {strongest['label']} ({strongest['score']}). "
        f"Watchlist: {weakest['label']} ({weakest['score']})."
    )
    if run.get("status") == "FAILED":
        summary = f"Run failed before completion. {summary}"

    return {
        "status": run.get("status"),
        "run_id": run.get("id"),
        "generated_at": _now(),
        "overall_score": overall_score,
        "weights": [
            {"key": key, "label": label, "weight": weight}
            for key, label, weight in (
                ("business_impact", "Business Impact", WEIGHTS["business_impact"]),
                ("task_success", "Task Success", WEIGHTS["task_success"]),
                ("planning_quality", "Planning Quality", WEIGHTS["planning_quality"]),
                ("autonomy", "Autonomy", WEIGHTS["autonomy"]),
                ("efficiency", "Efficiency", WEIGHTS["efficiency"]),
                ("reliability", "Reliability", WEIGHTS["reliability"]),
            )
        ],
        "categories": categories,
        "summary": summary,
        "strongest_category": strongest["key"],
        "weakest_category": weakest["key"],
    }
