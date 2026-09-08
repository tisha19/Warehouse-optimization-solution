"""Repeatable regression and hard-constraint harness for agent workflows."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


class ConstraintViolation(ValueError):
    pass


def validate_moves(moves: Iterable[Mapping[str, Any]], available_slots: Iterable[str], slot_capacity: Mapping[str, float], sku_quantities: Mapping[str, float]) -> List[str]:
    available = set(available_slots)
    used = set()
    violations: List[str] = []
    for move in moves:
        sku = move.get("sku_id", move.get("sku"))
        destination = move.get("to_slot")
        if not sku or not destination:
            violations.append("Every move requires sku_id/sku and to_slot")
            continue
        if destination not in available:
            violations.append(f"Destination slot is unavailable: {destination}")
        if destination in used:
            violations.append(f"Destination slot is duplicated: {destination}")
        if destination in available and sku_quantities.get(sku, 0) > slot_capacity.get(destination, 0):
            violations.append(f"Capacity exceeded for {sku} in {destination}")
        used.add(destination)
    return violations


def run_regression_suite(cases: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    results = []
    for case in cases:
        violations = validate_moves(case.get("moves", []), case.get("available_slots", []), case.get("slot_capacity", {}), case.get("sku_quantities", {}))
        expected = case.get("expected_violations", [])
        passed = sorted(violations) == sorted(expected)
        results.append({"case_id": case.get("case_id", "unknown"), "passed": passed, "violations": violations, "expected_violations": expected})
    return {"passed": all(item["passed"] for item in results), "cases": results}


DEFAULT_REGRESSION_CASES = [
    {"case_id": "valid_move", "moves": [{"sku_id": "SKU1", "to_slot": "A001"}], "available_slots": ["A001"], "slot_capacity": {"A001": 100}, "sku_quantities": {"SKU1": 50}, "expected_violations": []},
    {"case_id": "duplicate_destination", "moves": [{"sku_id": "SKU1", "to_slot": "A001"}, {"sku_id": "SKU2", "to_slot": "A001"}], "available_slots": ["A001"], "slot_capacity": {"A001": 100}, "sku_quantities": {"SKU1": 50, "SKU2": 50}, "expected_violations": ["Destination slot is duplicated: A001"]},
    {"case_id": "capacity_violation", "moves": [{"sku_id": "SKU1", "to_slot": "A001"}], "available_slots": ["A001"], "slot_capacity": {"A001": 10}, "sku_quantities": {"SKU1": 50}, "expected_violations": ["Capacity exceeded for SKU1 in A001"]},
    {"case_id": "unavailable_slot", "moves": [{"sku_id": "SKU1", "to_slot": "Z999"}], "available_slots": ["A001"], "slot_capacity": {"A001": 100}, "sku_quantities": {"SKU1": 50}, "expected_violations": ["Destination slot is unavailable: Z999"]},
]
