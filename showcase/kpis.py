"""Warehouse KPIs measured from the current WMS state.

Everything here is arithmetic over the live snapshot: no targets, no
benchmarks, no estimates. If a number cannot be derived it is not shown.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

# A pick is a round trip between the slot and the pick face.
TRIP_FACTOR = 2
FORWARD_PICK_ZONE = 1


def _daily_demand(forecast_rows: List[Mapping[str, Any]], horizon_days: int) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    days: set[int] = set()
    for row in forecast_rows:
        day = int(row.get("forecast_day", 0))
        if day <= horizon_days:
            days.add(day)
            key = str(row.get("sku_id"))
            totals[key] = totals.get(key, 0.0) + float(row.get("forecast_qty", 0.0))
    span = max(len(days), 1)
    return {sku: total / span for sku, total in totals.items()}


def warehouse_kpis(source: Mapping[str, Any], horizon_days: int = 7) -> Dict[str, Any]:
    wms = source.get("wms", {})
    layout = wms.get("warehouse_layout", [])
    occupancy = wms.get("slot_occupancy", [])
    inventory = wms.get("inventory_snapshot", [])
    sku_rows = source.get("erp", {}).get("sku_master", {}).get("sku_master", [])
    forecast_rows = source.get("forecast", {}).get("forecast", [])

    distance_by_slot = {str(r.get("slot_id")): float(r.get("distance_to_picking_m", 0.0)) for r in layout}
    zone_by_slot = {str(r.get("slot_id")): int(r.get("zone_id", 0)) for r in layout}
    current_slot: Dict[str, str] = {}
    for row in occupancy:
        current_slot.setdefault(str(row.get("sku_id")), str(row.get("slot_id")))

    demand = _daily_demand(forecast_rows, horizon_days)
    sku_by_id = {str(row.get("sku_id")): row for row in sku_rows}

    total_picks = 0.0
    total_distance_m = 0.0
    a_class_picks = 0.0
    a_class_forward = 0.0
    for sku_id, picks in demand.items():
        slot = current_slot.get(sku_id)
        if not slot:
            continue
        total_picks += picks
        total_distance_m += picks * distance_by_slot.get(slot, 0.0) * TRIP_FACTOR
        if str(sku_by_id.get(sku_id, {}).get("abc_class")) == "A":
            a_class_picks += picks
            if zone_by_slot.get(slot) == FORWARD_PICK_ZONE:
                a_class_forward += picks

    blocked = sum(1 for row in layout if str(row.get("operational_status", "ACTIVE")).upper() != "ACTIVE")
    below_reorder = sum(1 for row in inventory if float(row.get("current_stock", 0)) < float(row.get("reorder_point", 0)))
    active_slots = len(layout) - blocked

    return {
        "daily_travel_km": round(total_distance_m / 1000.0, 1),
        "avg_distance_per_pick_m": round(total_distance_m / max(total_picks, 1.0), 1),
        "daily_picks": round(total_picks),
        "forward_pick_coverage_pct": round(a_class_forward / max(a_class_picks, 1.0) * 100, 1),
        "slot_utilisation_pct": round(len(occupancy) / max(active_slots, 1) * 100, 1),
        "blocked_slots": blocked,
        "lines_below_reorder": below_reorder,
    }


def detect_problems(kpis: Mapping[str, Any], source: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Findings stated as measured facts, each with the number behind it."""
    problems: List[Dict[str, Any]] = []
    coverage = float(kpis["forward_pick_coverage_pct"])
    if coverage < 60:
        problems.append({
            "id": "forward-pick-coverage",
            "severity": "high" if coverage < 30 else "medium",
            "title": "Fast-moving stock is not in the forward pick face",
            "detail": f"Only {coverage}% of class A demand is picked from zone A. The rest is walked to from reserve.",
            "metric": f"{coverage}%",
        })
    if float(kpis["avg_distance_per_pick_m"]) > 40:
        problems.append({
            "id": "travel-per-pick",
            "severity": "high",
            "title": "Average pick trip is long",
            "detail": f"Each pick walks {kpis['avg_distance_per_pick_m']}m round trip, {kpis['daily_travel_km']}km per day across the operation.",
            "metric": f"{kpis['avg_distance_per_pick_m']}m",
        })
    if int(kpis["lines_below_reorder"]) > 0:
        problems.append({
            "id": "below-reorder",
            "severity": "medium",
            "title": "Lines are below their reorder point",
            "detail": f"{kpis['lines_below_reorder']} SKUs sit below reorder point and depend on inbound arriving on time.",
            "metric": str(kpis["lines_below_reorder"]),
        })
    if int(kpis["blocked_slots"]) > 0:
        problems.append({
            "id": "blocked-slots",
            "severity": "low",
            "title": "Slots are out of service",
            "detail": f"{kpis['blocked_slots']} slots are blocked for maintenance and excluded from slotting.",
            "metric": str(kpis["blocked_slots"]),
        })
    return problems
