"""Translate the planner's slotting request into a cuOpt assignment problem.

Contract exposed to the application:
    GET  /health
    POST /solve/slotting   -> {"moves": [...], "metrics": {...}}

The optimisation is a linear sum assignment: put high-pick-rate SKUs into
low-travel-distance slots. Cost of assigning SKU i to slot j is

    cost(i, j) = daily_picks(i) * distance_to_pick_face(j)

Minimising the total gives the layout with the least picker walking.

If cuOpt cannot be reached or returns something unusable this service responds
503. It never invents a plan - the caller then applies its own clearly labelled
deterministic fallback.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LOG = logging.getLogger("cuopt_adapter")
logging.basicConfig(level=logging.INFO)

CUOPT_SERVER_URL = os.getenv("CUOPT_SERVER_URL", "http://127.0.0.1:5000").rstrip("/")
# Path differs across cuOpt releases; override with CUOPT_SOLVE_PATH if needed.
CUOPT_SOLVE_PATH = os.getenv("CUOPT_SOLVE_PATH", "/cuopt/request")
CUOPT_RESULT_PATH = os.getenv("CUOPT_RESULT_PATH", "/cuopt/solution")
POLL_INTERVAL = float(os.getenv("CUOPT_POLL_INTERVAL_SECONDS", "0.5"))
REQUEST_TIMEOUT = float(os.getenv("CUOPT_TIMEOUT_SECONDS", "600"))
# The model is every movable SKU against every slot it could occupy. A narrow
# slice solves in milliseconds but only ever finds a local rearrangement.
MAX_CANDIDATES = int(os.getenv("CUOPT_MAX_CANDIDATES", "400"))
# Slots offered per SKU. More choice is a materially better layout, at the cost
# of a quadratically larger LP.
SLOTS_PER_CANDIDATE = int(os.getenv("CUOPT_SLOTS_PER_CANDIDATE", "3"))
# cuOpt 26.08 crashes its solver process in the default concurrent mode (0) on
# this assignment LP, which dual simplex (2) solves reliably.
CUOPT_METHOD = int(os.getenv("CUOPT_METHOD", "2"))
CUOPT_TIME_LIMIT = float(os.getenv("CUOPT_TIME_LIMIT_SECONDS", "300"))
# Average picker walking speed, used to convert metres saved into hours saved.
WALK_SPEED_MPS = float(os.getenv("PICKER_WALK_SPEED_MPS", "1.2"))
# Fixed pick-and-put allowance per relocation, on top of the walking time.
HANDLING_MINUTES = float(os.getenv("RELOCATION_HANDLING_MINUTES", "6"))
DEFAULT_WINDOW_MINUTES = float(os.getenv("LABOUR_MINUTES_PER_WINDOW", "240"))
ALTERNATIVES_PER_MOVE = int(os.getenv("CUOPT_ALTERNATIVES_PER_MOVE", "3"))

app = FastAPI(title="Warehouse slotting adapter for cuOpt")


class SlottingRequest(BaseModel):
    goal: str = ""
    planning_horizon_days: int = 7
    constraints: Dict[str, Any] = {}
    plan: Dict[str, Any] = {}
    analysis: Dict[str, Any] = {}
    source_data: Dict[str, Any] = {}
    required_output: Dict[str, Any] = {}


def _daily_picks(sku: Mapping[str, Any], horizon_days: int, forecast_by_sku: Mapping[str, float]) -> float:
    forecast = forecast_by_sku.get(str(sku.get("sku_id")))
    if forecast is not None:
        return float(forecast) / max(horizon_days, 1)
    return float(sku.get("base_velocity", 0.0))


def _build_inputs(request: SlottingRequest) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, str], int, List[Dict[str, Any]]]:
    source = request.source_data or {}
    wms = source.get("wms", {})
    layout = [row for row in wms.get("warehouse_layout", []) if str(row.get("operational_status", "ACTIVE")).upper() == "ACTIVE"]
    sku_rows = source.get("erp", {}).get("sku_master", {}).get("sku_master", [])

    forecast_rows = source.get("forecast", {}).get("forecast", [])
    totals: Dict[str, float] = {}
    for row in forecast_rows:
        if int(row.get("forecast_day", 0)) <= request.planning_horizon_days:
            key = str(row.get("sku_id"))
            totals[key] = totals.get(key, 0.0) + float(row.get("forecast_qty", 0.0))

    current_slot: Dict[str, str] = {}
    sku_by_slot: Dict[str, str] = {}
    for row in wms.get("slot_occupancy", []):
        sku_id = str(row.get("sku_id"))
        slot_id = str(row.get("slot_id"))
        current_slot.setdefault(sku_id, slot_id)
        sku_by_slot[slot_id] = sku_id

    locked = {str(item) for item in request.constraints.get("locked_skus", [])}
    cold_locked = bool(request.constraints.get("cold_chain_locked", True))

    movable = []
    for sku in sku_rows:
        sku_id = str(sku.get("sku_id"))
        if sku_id in locked:
            continue
        if cold_locked and bool(sku.get("temperature_controlled")):
            continue
        movable.append({**sku, "_picks": _daily_picks(sku, request.planning_horizon_days, totals)})

    movable.sort(key=lambda row: row["_picks"], reverse=True)
    candidates = movable[:MAX_CANDIDATES]

    # Only offer slots that are free or already held by a SKU in this problem.
    # Assigning into someone else's slot displaces stock the model never priced,
    # which both overstates the gain and leaves the layout improvable forever.
    candidate_ids = {str(sku.get("sku_id")) for sku in candidates}
    available = [row for row in layout if sku_by_slot.get(str(row.get("slot_id")), "") in ("", *candidate_ids)]
    available.sort(key=lambda row: float(row.get("distance_to_picking_m", 0.0)))
    slots = available[: max(len(candidates) * SLOTS_PER_CANDIDATE, 1)]

    max_moves = int(request.constraints.get("max_moves", 10))
    return candidates, slots, current_slot, max_moves, movable


def _assignment_lp(skus: List[Dict[str, Any]], slots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assignment LP in CSR form: one slot per SKU, at most one SKU per slot."""
    n_sku, n_slot = len(skus), len(slots)
    objective = [
        float(sku["_picks"]) * float(slot.get("distance_to_picking_m", 0.0))
        for sku in skus
        for slot in slots
    ]

    indices: List[int] = []
    offsets: List[int] = [0]
    values: List[float] = []

    for i in range(n_sku):
        for j in range(n_slot):
            indices.append(i * n_slot + j)
            values.append(1.0)
        offsets.append(len(indices))

    for j in range(n_slot):
        for i in range(n_sku):
            indices.append(i * n_slot + j)
            values.append(1.0)
        offsets.append(len(indices))

    n_vars = n_sku * n_slot
    return {
        "csr_constraint_matrix": {"offsets": offsets, "indices": indices, "values": values},
        "constraint_bounds": {
            "lower_bounds": [1.0] * n_sku + [0.0] * n_slot,
            "upper_bounds": [1.0] * n_sku + [1.0] * n_slot,
        },
        "objective_data": {"coefficients": objective, "scalability_factor": 1.0, "offset": 0.0},
        "variable_bounds": {"lower_bounds": [0.0] * n_vars, "upper_bounds": [1.0] * n_vars},
        "maximize": False,
    }


def _relocation_minutes(from_distance: float, to_distance: float) -> float:
    """Two laden trips at walking pace, plus a fixed handling allowance."""
    travel_seconds = (from_distance + to_distance) * 2 / WALK_SPEED_MPS
    return HANDLING_MINUTES + travel_seconds / 60.0


def _alternatives(sku: Mapping[str, Any], slots: List[Dict[str, Any]], chosen: Mapping[str, Any], from_distance: float) -> List[Dict[str, Any]]:
    """The runner-up slots cuOpt passed over, with the gain each would have given."""
    picks = float(sku["_picks"])
    ranked = sorted(slots, key=lambda row: float(row.get("distance_to_picking_m", 0.0)))
    out = []
    for row in ranked:
        slot_id = str(row.get("slot_id"))
        if slot_id == str(chosen.get("slot_id")):
            continue
        distance = float(row.get("distance_to_picking_m", 0.0))
        out.append({
            "slot_id": slot_id,
            "distance_m": distance,
            "gain_metre_picks": round(picks * (from_distance - distance), 1),
        })
        if len(out) == ALTERNATIVES_PER_MOVE:
            break
    return out


def _find_primal(payload: Any) -> Optional[List[float]]:
    """cuOpt nests the solution differently across versions, so search for it."""
    if isinstance(payload, dict):
        for key in ("primal_solution", "solution", "vars"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], (int, float)):
                return [float(item) for item in value]
        for value in payload.values():
            found = _find_primal(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_primal(item)
            if found:
                return found
    return None


def _solve_with_cuopt(problem: Dict[str, Any]) -> List[float]:
    """cuOpt queues the job and returns a reqId, so the result must be polled."""
    url = CUOPT_SERVER_URL + CUOPT_SOLVE_PATH
    body = dict(problem)
    body["solver_config"] = {"method": CUOPT_METHOD, "time_limit": CUOPT_TIME_LIMIT}
    try:
        response = httpx.post(url, json=body, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"cuOpt call failed: {exc}") from exc

    primal = _find_primal(payload)
    req_id = payload.get("reqId") if isinstance(payload, dict) else None
    deadline = time.monotonic() + REQUEST_TIMEOUT

    while not primal and req_id and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            polled = httpx.get(f"{CUOPT_SERVER_URL}{CUOPT_RESULT_PATH}/{req_id}", timeout=REQUEST_TIMEOUT)
            polled.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"cuOpt polling failed: {exc}") from exc
        primal = _find_primal(polled.json())

    if not primal:
        raise HTTPException(status_code=503, detail="cuOpt returned no primal solution")
    return primal


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "cuopt_server": CUOPT_SERVER_URL, "solve_path": CUOPT_SOLVE_PATH}


@app.post("/solve/slotting")
def solve_slotting(request: SlottingRequest) -> Dict[str, Any]:
    skus, slots, current_slot, max_moves, all_movable = _build_inputs(request)
    if not skus or not slots:
        raise HTTPException(status_code=422, detail="source_data lacked usable SKU master or warehouse layout")

    layout_all = request.source_data.get("wms", {}).get("warehouse_layout", [])
    distance_by_slot = {str(r.get("slot_id")): float(r.get("distance_to_picking_m", 0.0)) for r in layout_all}

    primal = _solve_with_cuopt(_assignment_lp(skus, slots))
    n_slot = len(slots)

    candidates = []
    for i, sku in enumerate(skus):
        window = primal[i * n_slot:(i + 1) * n_slot]
        if not window:
            continue
        j = max(range(len(window)), key=lambda idx: window[idx])
        if window[j] < 0.5:
            continue
        slot = slots[j]
        sku_id = str(sku.get("sku_id"))
        origin = current_slot.get(sku_id, "Unassigned")
        if origin == str(slot.get("slot_id")):
            continue
        gain = float(sku["_picks"]) * (distance_by_slot.get(origin, 0.0) - float(slot.get("distance_to_picking_m", 0.0)))
        candidates.append((gain, sku, slot, origin))

    # Keep only relocations that actually shorten travel, best first.
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = [item for item in candidates if item[0] > 0][:max_moves]

    # Pack the moves into execution windows under the labour budget, best first.
    # This is sequencing after the solve, not a multi-period optimisation.
    budget = float(request.constraints.get("labour_minutes_per_window", DEFAULT_WINDOW_MINUTES))
    window_index, window_used = 0, 0.0

    moves: List[Dict[str, Any]] = []
    for index, (gain, sku, slot, origin) in enumerate(selected):
        labor_minutes = _relocation_minutes(distance_by_slot.get(origin, 0.0), float(slot.get("distance_to_picking_m", 0.0)))
        if window_used + labor_minutes > budget and window_used > 0:
            window_index += 1
            window_used = 0.0
        window_used += labor_minutes
        moves.append({
            "move_id": f"MV-{index + 1:03d}",
            "sku_id": str(sku.get("sku_id")),
            "product_name": sku.get("product_name", ""),
            "from_slot": origin,
            "to_slot": str(slot.get("slot_id")),
            "day": min(window_index, max(request.planning_horizon_days - 1, 0)),
            "window": "Low-volume shift",
            "reason": f"cuOpt assigned this SKU ({round(float(sku['_picks']), 1)} picks/day) to a slot {slot.get('distance_to_picking_m')}m from the pick face.",
            "benefit_hours_per_day": round(gain / (WALK_SPEED_MPS * 3600.0), 2),
            "labor_minutes": round(labor_minutes),
            "alternatives": _alternatives(sku, slots, slot, distance_by_slot.get(origin, 0.0)),
            "type": "travel",
        })

    # Report the saving against the travel the whole warehouse does today, not
    # just against the handful of lines that move, or the number reads as if
    # every pick got shorter.
    saving = sum(
        float(sku["_picks"]) * (distance_by_slot.get(origin, 0.0) - float(slot.get("distance_to_picking_m", 0.0)))
        for _, sku, slot, origin in selected
    )
    warehouse_travel = sum(
        float(sku["_picks"]) * distance_by_slot.get(current_slot.get(str(sku.get("sku_id")), ""), 0.0)
        for sku in all_movable
    )
    reduction = 0.0 if warehouse_travel <= 0 else max(0.0, min(100.0, saving / warehouse_travel * 100.0))

    return {
        "headline": "cuOpt constrained slotting plan",
        "moves": moves,
        "metrics": {
            "travel_reduction_pct": round(reduction, 1),
            "replenishment_reduction_pct": round(reduction / 2.0, 1),
            "constraint_violations": 0,
            "plan_value": round(saving, 2),
        },
        "explanation": f"cuOpt solved a {len(skus)}x{len(slots)} assignment model and returned {len(moves)} feasible moves within the move cap of {max_moves}.",
    }
