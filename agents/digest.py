"""Compact, decision-relevant summary of warehouse state for the language models.

The optimiser needs every row; the LLMs do not. They decide objectives, weights
and which constraints bind, then explain the result. Feeding them raw WMS/ERP/
forecast rows is slow, unverifiable and leaks the dataset to the model endpoint,
so everything they see is aggregated here first.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping

TOP_SKUS = 15


def _demand_by_sku(forecast_rows: List[Mapping[str, Any]], horizon_days: int) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    for row in forecast_rows:
        if int(row.get("forecast_day", 0)) <= horizon_days:
            totals[str(row.get("sku_id"))] += float(row.get("forecast_qty", 0.0))
    return totals


def warehouse_digest(source_data: Mapping[str, Any], constraints: Mapping[str, Any], horizon_days: int = 7, top_n: int = TOP_SKUS) -> Dict[str, Any]:
    wms = source_data.get("wms", {})
    layout = wms.get("warehouse_layout", [])
    occupancy = wms.get("slot_occupancy", [])
    sku_rows = source_data.get("erp", {}).get("sku_master", {}).get("sku_master", [])
    forecast_rows = source_data.get("forecast", {}).get("forecast", [])

    distance_by_slot = {str(r.get("slot_id")): float(r.get("distance_to_picking_m", 0.0)) for r in layout}
    zone_by_slot = {str(r.get("slot_id")): int(r.get("zone_id", 0)) for r in layout}

    slots_per_zone: Dict[int, int] = defaultdict(int)
    distance_per_zone: Dict[int, float] = defaultdict(float)
    for row in layout:
        zone = int(row.get("zone_id", 0))
        slots_per_zone[zone] += 1
        distance_per_zone[zone] += float(row.get("distance_to_picking_m", 0.0))

    occupied_per_zone: Dict[int, int] = defaultdict(int)
    current_slot: Dict[str, str] = {}
    for row in occupancy:
        occupied_per_zone[int(row.get("zone_id", 0))] += 1
        current_slot.setdefault(str(row.get("sku_id")), str(row.get("slot_id")))

    zones = [
        {
            "zone": zone,
            "slots": slots_per_zone[zone],
            "occupied": occupied_per_zone.get(zone, 0),
            "free": slots_per_zone[zone] - occupied_per_zone.get(zone, 0),
            "avg_distance_m": round(distance_per_zone[zone] / max(slots_per_zone[zone], 1), 1),
        }
        for zone in sorted(slots_per_zone)
    ]

    demand = _demand_by_sku(forecast_rows, horizon_days)
    locked = {str(s) for s in constraints.get("locked_skus", [])}

    ranked = sorted(sku_rows, key=lambda r: demand.get(str(r.get("sku_id")), 0.0), reverse=True)[:top_n]
    top_skus = []
    for row in ranked:
        sku_id = str(row.get("sku_id"))
        slot = current_slot.get(sku_id, "unassigned")
        top_skus.append({
            "sku": sku_id,
            "name": row.get("product_name"),
            "class": row.get("abc_class"),
            "demand_per_day": round(demand.get(sku_id, 0.0) / max(horizon_days, 1), 1),
            "slot": slot,
            "zone": zone_by_slot.get(slot),
            "distance_m": distance_by_slot.get(slot),
            "cold_chain": bool(row.get("temperature_controlled")),
            "promo_uplift_pct": row.get("promotion_uplift_pct", 0),
            "locked": sku_id in locked,
        })

    return {
        "counts": {
            "skus": len(sku_rows),
            "slots": len(layout),
            "occupied_slots": len(occupancy),
            "forecast_rows": len(forecast_rows),
            "cold_chain_skus": sum(1 for r in sku_rows if r.get("temperature_controlled")),
        },
        "horizon_days": horizon_days,
        "zones": zones,
        "top_skus_by_demand": top_skus,
        "promotions": [
            {"sku": str(r.get("sku_id")), "uplift_pct": r.get("promotion_uplift_pct")}
            for r in sku_rows if float(r.get("promotion_uplift_pct", 0) or 0) > 0
        ][:top_n],
        "constraints": dict(constraints),
    }
