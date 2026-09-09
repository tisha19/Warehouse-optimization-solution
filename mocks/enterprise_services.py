"""Local synthetic service gateway for WMS, ERP, and forecast endpoints.

Run:
    python -m mocks.enterprise_services --port 9100

Point only WMS_URL, ERP_URL, and FORECAST_URL at http://localhost:9100. NVIDIA,
NeMo, Guardrails, OpenShell, and cuOpt URLs must point to real services.

The data is randomised but shaped like a real grocery distribution centre:
demand follows an ABC (Pareto) split, travel distance comes from where a slot
physically sits, stock cover is derived from velocity and supplier lead time,
and the current slotting has drifted, which is what gives the optimiser
something real to solve. A seed makes any dataset reproducible; set
WAREHOUSE_DATA_SEED=random for a different warehouse on every start.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Mapping

SKU_COUNT = 100
ZONE_COUNT = 6
SLOTS_PER_ZONE = 83
FORECAST_HORIZON_DAYS = 14
TARGET_OCCUPIED_SLOTS = 300
DEFAULT_SEED = 7

# category, temperature controlled, unit weight kg, unit cost, product nouns
CATALOGUE = (
    ("Beverages", False, (0.35, 1.7), (0.40, 2.60), ("Sparkling Water", "Cola", "Orange Juice", "Energy Drink", "Iced Tea", "Tonic Water")),
    ("Chilled Dairy", True, (0.25, 1.2), (0.90, 4.50), ("Whole Milk", "Greek Yoghurt", "Salted Butter", "Cheddar Block", "Cream Cheese")),
    ("Frozen Food", True, (0.40, 1.5), (1.20, 6.00), ("Garden Peas", "Vanilla Ice Cream", "Stone-baked Pizza", "Fish Fillets")),
    ("Dry Goods", False, (0.50, 2.5), (0.80, 7.50), ("Basmati Rice", "Penne Pasta", "Ground Coffee", "Caster Sugar", "Plain Flour")),
    ("Snacks", False, (0.10, 0.6), (0.60, 3.80), ("Salted Crisps", "Chocolate Bars", "Mixed Nuts", "Cereal Bars")),
    ("Household", False, (0.60, 3.0), (1.50, 12.00), ("Laundry Pods", "Kitchen Roll", "Dish Soap", "Bin Liners")),
    ("Personal Care", False, (0.15, 0.9), (1.20, 9.00), ("Shampoo", "Toothpaste", "Hand Wash", "Shower Gel")),
)
PACK_SIZES = {
    "Beverages": ("6pk", "12pk", "24pk", "750ml", "1.5L"),
    "Chilled Dairy": ("4pk", "6pk", "500g", "1kg"),
    "Frozen Food": ("500g", "1kg", "2.5kg", "case of 8"),
    "Dry Goods": ("500g", "1kg", "2.5kg", "5kg"),
    "Snacks": ("6pk", "12pk", "150g", "300g"),
    "Household": ("6pk", "12pk", "case of 8", "2L"),
    "Personal Care": ("250ml", "500ml", "6pk"),
}
CATEGORY_SUPPLIERS = {
    "Beverages": ("Baltic Beverages", "Northwind Foods"),
    "Chilled Dairy": ("Crestline Dairy",),
    "Frozen Food": ("Northwind Foods", "Crestline Dairy"),
    "Dry Goods": ("Harbour Dry Goods", "Northwind Foods"),
    "Snacks": ("Harbour Dry Goods", "Northwind Foods"),
    "Household": ("Vale Household",),
    "Personal Care": ("Lumen Personal Care",),
}
BRANDS = ("Ashford", "Bramley", "Calder", "Deepwell", "Elmgrove", "Fairholt", "Granby", "Hollins", "Ivorycrest", "Keswick", "Langmere", "Marlow")

# Zone 1 is the forward pick face; the rest go progressively deeper into the building.
ZONE_BASE_DISTANCE_M = {1: 2.0, 2: 15.0, 3: 25.0, 4: 40.0, 5: 55.0, 6: 70.0}
CHILLED_ZONE = 2
AISLES_PER_ZONE = 4
BAYS_PER_AISLE = 7
LEVELS_PER_BAY = 3

# Velocity band and target days of cover per ABC class.
CLASS_VELOCITY = {"A": (500.0, 2200.0), "B": (100.0, 500.0), "C": (5.0, 100.0)}
CLASS_DAYS_OF_COVER = {"A": 10, "B": 21, "C": 45}
CLASS_MIX = {"A": 20, "B": 30, "C": 50}
# Grocery demand peaks towards the weekend, Monday to Sunday.
WEEKDAY_FACTOR = (1.05, 0.95, 0.98, 1.02, 1.25, 1.35, 0.80)

HERO_SKU_ID = "SKU-100"


def resolve_seed(value: str | int | None = None) -> int:
    """WAREHOUSE_DATA_SEED=random gives a different warehouse on every start."""
    raw = value if value is not None else os.getenv("WAREHOUSE_DATA_SEED", DEFAULT_SEED)
    if isinstance(raw, str) and raw.strip().lower() == "random":
        return random.SystemRandom().randrange(1, 2**31)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SEED


def _build_sku_master(rng: random.Random) -> List[Dict[str, Any]]:
    classes = [abc for abc, count in CLASS_MIX.items() for _ in range(count)]
    rng.shuffle(classes)
    # The hero line is class A by definition; swap rather than overwrite so the mix holds.
    hero_index = SKU_COUNT - 1
    if classes[hero_index] != "A":
        classes[classes.index("A")], classes[hero_index] = classes[hero_index], "A"
    used_names: set[str] = set()
    skus: List[Dict[str, Any]] = []

    for number in range(1, SKU_COUNT + 1):
        sku_id = f"SKU-{number:03d}"
        abc_class = classes[number - 1]
        category, temperature_controlled, unit_weight, unit_cost, nouns = rng.choice(CATALOGUE)
        noun = rng.choice(nouns)
        name = f"{rng.choice(BRANDS)} {noun} {rng.choice(PACK_SIZES[category])}"
        for _ in range(20):
            if name not in used_names:
                break
            name = f"{rng.choice(BRANDS)} {noun} {rng.choice(PACK_SIZES[category])}"
        low, high = CLASS_VELOCITY[abc_class]
        base_velocity = round(rng.uniform(low, high), 2)

        if sku_id == HERO_SKU_ID:
            # The dashboard narrative refers to this line by name.
            category, temperature_controlled = "Beverages", False
            unit_weight, unit_cost = (0.35, 1.7), (0.40, 2.60)
            name, base_velocity = "Sparkling Water 12pk", 1800.0

        used_names.add(name)
        case_pack = rng.choice((6, 8, 12, 24))
        skus.append({
            "sku_id": sku_id,
            "product_name": name,
            "category": category,
            "abc_class": abc_class,
            "base_velocity": base_velocity,
            "temperature_controlled": temperature_controlled,
            "case_pack": case_pack,
            "weight_kg": round(rng.uniform(*unit_weight) * case_pack, 1),
            "unit_cost": round(rng.uniform(*unit_cost), 2),
            "supplier": rng.choice(CATEGORY_SUPPLIERS[category]),
            "lead_time_days": rng.choice((2, 3, 4, 5, 7)),
            "promotion_uplift_pct": 0,
        })
    return skus


def _apply_promotions(rng: random.Random, skus: List[Dict[str, Any]]) -> None:
    fast_movers = [sku for sku in skus if sku["abc_class"] in ("A", "B") and sku["sku_id"] != HERO_SKU_ID]
    for sku in rng.sample(fast_movers, min(5, len(fast_movers))):
        sku["promotion_uplift_pct"] = rng.choice((25, 30, 40, 50, 60))
        sku["promotion_start_day"] = rng.randint(1, 4)
        sku["promotion_end_day"] = sku["promotion_start_day"] + rng.randint(3, 6)
    for sku in skus:
        if sku["sku_id"] == HERO_SKU_ID:
            sku.update({"promotion_uplift_pct": 40, "promotion_start_day": 1, "promotion_end_day": 5})


def _build_layout(rng: random.Random) -> List[Dict[str, Any]]:
    layout: List[Dict[str, Any]] = []
    for zone in range(1, ZONE_COUNT + 1):
        base = ZONE_BASE_DISTANCE_M[zone]
        for index in range(SLOTS_PER_ZONE):
            level = index % LEVELS_PER_BAY
            bay = (index // LEVELS_PER_BAY) % BAYS_PER_AISLE
            aisle = index // (LEVELS_PER_BAY * BAYS_PER_AISLE)
            layout.append({
                "slot_id": f"{chr(64 + zone)}{index + 1:04d}",
                "zone_id": zone,
                "aisle": aisle + 1,
                "bay": bay + 1,
                "level": level + 1,
                # Walking distance is driven by how deep the slot sits, plus a
                # small penalty for reaching upper levels.
                "distance_to_picking_m": round(base + aisle * 1.6 + bay * 0.5 + level * 0.25, 1),
                "bin_capacity": 100 if level == 0 else 80,
                "temperature_controlled": zone == CHILLED_ZONE,
                "operational_status": "BLOCKED" if rng.random() < 0.02 else "ACTIVE",
            })
    return layout


def _build_occupancy(rng: random.Random, skus: List[Dict[str, Any]], layout: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Place stock as a drifted warehouse would have it, not as it should be."""
    active = [slot for slot in layout if slot["operational_status"] == "ACTIVE"]
    chilled = [slot for slot in active if slot["temperature_controlled"]]
    ambient = [slot for slot in active if not slot["temperature_controlled"]]
    rng.shuffle(chilled)
    rng.shuffle(ambient)

    # The hero line starts deep in the building so the plan has a headline move.
    deep = [slot for slot in ambient if slot["zone_id"] >= ZONE_COUNT - 1]
    hero_slot = deep[0] if deep else ambient[0]
    ambient.remove(hero_slot)

    occupancy: List[Dict[str, Any]] = []

    def place(sku: Mapping[str, Any], slot: Mapping[str, Any]) -> None:
        occupancy.append({
            "slot_id": slot["slot_id"],
            "sku_id": sku["sku_id"],
            "zone_id": slot["zone_id"],
            "quantity": rng.randint(10, int(slot["bin_capacity"])),
        })

    for sku in skus:
        if sku["sku_id"] == HERO_SKU_ID:
            place(sku, hero_slot)
            continue
        pool = chilled if sku["temperature_controlled"] else ambient
        if pool:
            place(sku, pool.pop())

    # Fast movers hold more than one pallet, which is what fills the rest of the racking.
    overflow_candidates = [sku for sku in skus if sku["abc_class"] in ("A", "B")]
    while len(occupancy) < TARGET_OCCUPIED_SLOTS and (chilled or ambient):
        sku = rng.choice(overflow_candidates)
        pool = chilled if sku["temperature_controlled"] else ambient
        if not pool:
            pool = ambient or chilled
        place(sku, pool.pop())
    return occupancy


def _build_inventory(rng: random.Random, skus: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    inventory = []
    for sku in skus:
        daily = sku["base_velocity"]
        cover_days = CLASS_DAYS_OF_COVER[sku["abc_class"]]
        safety_stock = round(daily * sku["lead_time_days"] * 1.3)
        current_stock = round(daily * cover_days * rng.uniform(0.55, 1.35))
        inventory.append({
            "sku_id": sku["sku_id"],
            "current_stock": current_stock,
            "safety_stock": safety_stock,
            "reorder_point": safety_stock + round(daily * sku["lead_time_days"]),
            "max_stock": round(daily * cover_days * 1.8),
            "days_of_cover": round(current_stock / max(daily, 1.0), 1),
        })
    return inventory


def _build_inbound(rng: random.Random, skus: List[Dict[str, Any]], inventory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Everything below its reorder point is already on order, plus routine replenishment."""
    sku_by_id = {sku["sku_id"]: sku for sku in skus}
    short = [row["sku_id"] for row in inventory if row["current_stock"] < row["reorder_point"]]
    routine = [sku["sku_id"] for sku in skus if sku["abc_class"] in ("A", "B") and sku["sku_id"] not in short]
    rng.shuffle(routine)
    ordered = short + routine[: max(0, 18 - len(short))]

    shipments = []
    for index, sku_id in enumerate(ordered, start=1):
        sku = sku_by_id[sku_id]
        shipments.append({
            "shipment_id": f"ASN-{index:04d}",
            "po_number": f"PO-{rng.randint(100000, 999999)}",
            "sku_id": sku_id,
            "supplier": sku["supplier"],
            "quantity": round(sku["base_velocity"] * rng.uniform(5, 15) / sku["case_pack"]) * sku["case_pack"],
            "eta_day": rng.randint(1, sku["lead_time_days"] + 2),
            "status": rng.choice(("IN_TRANSIT", "IN_TRANSIT", "BOOKED", "AT_GATE")),
            "expedited": sku_id in short and sku["abc_class"] == "A",
        })
    return shipments


def _build_forecast(rng: random.Random, skus: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    forecast = []
    for sku in skus:
        daily = sku["base_velocity"]
        trend = rng.uniform(-0.004, 0.010)
        start = sku.get("promotion_start_day", 0)
        end = sku.get("promotion_end_day", -1)
        for day in range(1, FORECAST_HORIZON_DAYS + 1):
            on_promotion = bool(sku["promotion_uplift_pct"]) and start <= day <= end
            uplift = 1 + (sku["promotion_uplift_pct"] / 100 if on_promotion else 0)
            quantity = daily * WEEKDAY_FACTOR[(day - 1) % 7] * (1 + trend * day) * uplift * rng.uniform(0.88, 1.12)
            forecast.append({
                "sku_id": sku["sku_id"],
                "forecast_day": day,
                "forecast_qty": round(max(quantity, 0.0)),
                "promotion": on_promotion,
            })
    return forecast


def generate_synthetic_data(seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    skus = _build_sku_master(rng)
    _apply_promotions(rng, skus)
    layout = _build_layout(rng)
    inventory = _build_inventory(rng, skus)
    return {
        "sku_master": skus,
        "warehouse_layout": layout,
        "inventory": inventory,
        "occupancy": _build_occupancy(rng, skus, layout),
        "inbound": _build_inbound(rng, skus, inventory),
        "forecast": _build_forecast(rng, skus),
        "seed": seed,
    }


class MockServiceState:
    def __init__(self, seed: int = DEFAULT_SEED):
        self.seed = seed
        self.data = generate_synthetic_data(seed)
        self.applied_moves: List[Mapping[str, Any]] = []

    def snapshot(self) -> Dict[str, Any]:
        return {
            "warehouse_layout": self.data["warehouse_layout"],
            "slot_occupancy": self.data["occupancy"],
            "inventory_snapshot": self.data["inventory"],
        }

    def sku_master(self) -> Dict[str, Any]:
        return {"sku_master": self.data["sku_master"]}

    def inbound(self) -> Dict[str, Any]:
        return {"inbound_shipments": self.data["inbound"]}

    def forecast(self, horizon_days: int) -> Dict[str, Any]:
        rows = [row for row in self.data["forecast"] if row["forecast_day"] <= horizon_days]
        return {"forecast": rows, "horizon_days": horizon_days}

    def apply_moves(self, moves: List[Mapping[str, Any]]) -> int:
        """Relocate stock so a committed plan actually changes the warehouse."""
        by_slot = {row["slot_id"]: row for row in self.data["occupancy"]}
        zone_by_slot = {slot["slot_id"]: slot["zone_id"] for slot in self.data["warehouse_layout"]}
        applied = 0
        for move in moves:
            source = str(move.get("from_slot") or move.get("from") or "")
            target = str(move.get("to_slot") or move.get("to") or "")
            row = by_slot.get(source)
            if not row or target not in zone_by_slot:
                continue
            displaced = by_slot.get(target)
            if displaced:
                displaced["slot_id"], displaced["zone_id"] = source, zone_by_slot[source]
                by_slot[source] = displaced
            else:
                by_slot.pop(source, None)
            row["slot_id"], row["zone_id"] = target, zone_by_slot[target]
            by_slot[target] = row
            applied += 1
        self.applied_moves.extend(moves)
        return applied



class MockHandler(BaseHTTPRequestHandler):
    state: MockServiceState

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            response = self.route(self.path, payload)
            self.send_json(200, response)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def route(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if path == "/warehouse/snapshot":
            return self.state.snapshot()
        if path == "/warehouse/moves":
            moves = list(payload.get("moves", []))
            self.state.applied_moves.extend(moves)
            return {"status": "APPLIED", "approval_id": payload.get("approval_id"), "moves": moves}
        if path == "/erp/sku-master":
            return self.state.sku_master()
        if path == "/erp/inbound-shipments":
            return self.state.inbound()
        if path == "/forecast":
            return self.state.forecast(int(payload.get("horizon_days", 14)))
        raise ValueError(f"Unknown mock endpoint: {path}")

    def send_json(self, status: int, body: Dict[str, Any]) -> None:
        encoded = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic WMS, ERP, and forecast services")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--seed", default=None, help="integer seed, or 'random' for a different warehouse each start")
    args = parser.parse_args()
    seed = resolve_seed(args.seed)
    MockHandler.state = MockServiceState(seed)
    server = ThreadingHTTPServer((args.host, args.port), MockHandler)
    print(f"Mock services listening at http://{args.host}:{args.port} (data seed {seed})")
    print("Set only WMS_URL, ERP_URL, and FORECAST_URL to this URL. Configure NVIDIA and cuOpt URLs separately with real services.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
