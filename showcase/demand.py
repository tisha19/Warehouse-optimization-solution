"""Demand signal derived from the forecast and promotion data.

Nothing here is a judgement or an estimate: the headline signal is whichever
promoted line carries the largest forecast uplift, and the series is that
line's actual forecast rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping

TOP_MOVERS = 8


def _series(forecast_rows: List[Mapping[str, Any]], sku_id: str) -> List[Dict[str, Any]]:
    rows = [row for row in forecast_rows if str(row.get("sku_id")) == sku_id]
    rows.sort(key=lambda row: int(row.get("forecast_day", 0)))
    return [{"day": int(row["forecast_day"]), "qty": float(row["forecast_qty"]), "promotion": bool(row.get("promotion"))} for row in rows]


def daily_picks(source: Mapping[str, Any], horizon_days: int = 14) -> Dict[str, float]:
    """Forecast picks per day for every SKU in the horizon."""
    totals: Dict[str, float] = defaultdict(float)
    days: set[int] = set()
    for row in source.get("forecast", {}).get("forecast", []):
        day = int(row.get("forecast_day", 0))
        if day <= horizon_days:
            days.add(day)
            totals[str(row.get("sku_id"))] += float(row.get("forecast_qty", 0.0))
    span = max(len(days), 1)
    return {sku: total / span for sku, total in totals.items()}


def demand_signal(source: Mapping[str, Any], horizon_days: int = 14) -> Dict[str, Any]:
    sku_rows = source.get("erp", {}).get("sku_master", {}).get("sku_master", [])
    forecast_rows = source.get("forecast", {}).get("forecast", [])
    occupancy = source.get("wms", {}).get("slot_occupancy", [])
    layout = source.get("wms", {}).get("warehouse_layout", [])

    distance_by_slot = {str(r.get("slot_id")): float(r.get("distance_to_picking_m", 0.0)) for r in layout}
    slot_by_sku: Dict[str, str] = {}
    for row in occupancy:
        slot_by_sku.setdefault(str(row.get("sku_id")), str(row.get("slot_id")))

    totals = daily_picks(source, horizon_days)

    sku_by_id = {str(row.get("sku_id")): row for row in sku_rows}
    movers = []
    for sku_id, daily in totals.items():
        sku = sku_by_id.get(sku_id, {})
        base = float(sku.get("base_velocity", 0.0)) or daily
        slot = slot_by_sku.get(sku_id, "")
        movers.append({
            "sku_id": sku_id,
            "product_name": sku.get("product_name", sku_id),
            "abc_class": sku.get("abc_class", ""),
            "slot": slot,
            "distance_m": distance_by_slot.get(slot, 0.0),
            "picks_per_day": round(daily),
            "uplift_pct": round((daily / base - 1) * 100, 1) if base else 0.0,
            "promotion_uplift_pct": int(sku.get("promotion_uplift_pct", 0) or 0),
            "temperature_controlled": bool(sku.get("temperature_controlled")),
        })
    movers.sort(key=lambda row: row["picks_per_day"], reverse=True)

    promoted = [row for row in movers if row["promotion_uplift_pct"]]
    promoted.sort(key=lambda row: row["promotion_uplift_pct"] * row["picks_per_day"], reverse=True)
    headline = promoted[0] if promoted else (movers[0] if movers else None)

    return {
        "headline": headline and {
            **headline,
            "series": _series(forecast_rows, headline["sku_id"]),
        },
        "promoted_count": len(promoted),
        "top_movers": movers[:TOP_MOVERS],
        "horizon_days": horizon_days,
    }
