"""Local synthetic service gateway for WMS, ERP, and forecast endpoints.

Run:
    python -m mocks.enterprise_services --port 9100

Point only WMS_URL, ERP_URL, and FORECAST_URL at http://localhost:9100. NVIDIA,
NeMo, Guardrails, OpenShell, and cuOpt URLs must point to real services.
"""

from __future__ import annotations

import argparse
import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

def generate_synthetic_data(seed: int) -> Dict[str, Any]:
    random.seed(seed)
    skus = []
    for number in range(1, 101):
        abc_class = "A" if number <= 20 else "B" if number <= 50 else "C"
        base_velocity = random.uniform(500 if abc_class == "A" else 100 if abc_class == "B" else 10, 2000 if abc_class == "A" else 500 if abc_class == "B" else 100)
        sku_id = f"SKU-{number:03d}"
        skus.append({"sku_id": sku_id, "product_name": "Sparkling Water 12pk" if number == 100 else f"Warehouse Item {number:03d}", "abc_class": "A" if number == 100 else abc_class, "base_velocity": 1800.0 if number == 100 else round(base_velocity, 2), "min_stock": 50, "max_stock": 1000, "temperature_controlled": number % 17 == 0, "weight_kg": round(random.uniform(0.2, 35), 1), "promotion_uplift_pct": 40 if number == 100 else 0})
    layout = []
    for zone in range(1, 7):
        for number in range(1, 84):
            layout.append({"slot_id": f"{chr(64 + zone)}{number:04d}", "zone_id": zone, "distance_to_picking_m": {1: 2, 2: 15, 3: 25, 4: 40, 5: 55, 6: 70}[zone], "bin_capacity": 100, "operational_status": "ACTIVE"})
    inventory = [{"sku_id": sku["sku_id"], "current_stock": random.randint(50, 1000), "safety_stock": 50, "max_stock": 1000} for sku in skus]
    occupancy = [{"slot_id": slot["slot_id"], "sku_id": skus[index % len(skus)]["sku_id"], "zone_id": slot["zone_id"], "quantity": random.randint(10, 80)} for index, slot in enumerate(layout[:294])]
    forecast = [{"sku_id": sku["sku_id"], "forecast_day": day, "forecast_qty": round(sku["base_velocity"] * (1.4 if sku["sku_id"] == "SKU-100" and day <= 5 else random.uniform(0.8, 1.2)), 0), "promotion": sku["sku_id"] == "SKU-100" and day <= 5} for sku in skus for day in range(1, 15)]
    return {"sku_master": skus, "warehouse_layout": layout, "inventory": inventory, "occupancy": occupancy, "forecast": forecast}


class MockServiceState:
    def __init__(self, seed: int = 7):
        random.seed(seed)
        self.data = generate_synthetic_data(seed)
        self.applied_moves = []

    def snapshot(self) -> Dict[str, Any]:
        return {
            "warehouse_layout": self.data["warehouse_layout"],
            "slot_occupancy": self.data["occupancy"],
            "inventory_snapshot": self.data["inventory"],
        }

    def sku_master(self) -> Dict[str, Any]:
        return {"sku_master": self.data["sku_master"]}

    def inbound(self) -> Dict[str, Any]:
        return {"inbound_shipments": []}

    def forecast(self, horizon_days: int) -> Dict[str, Any]:
        rows = [row for row in self.data["forecast"] if row["forecast_day"] <= horizon_days]
        return {"forecast": rows, "horizon_days": horizon_days}


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
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    MockHandler.state = MockServiceState(args.seed)
    server = ThreadingHTTPServer((args.host, args.port), MockHandler)
    print(f"Mock services listening at http://{args.host}:{args.port}")
    print("Set only WMS_URL, ERP_URL, and FORECAST_URL to this URL. Configure NVIDIA and cuOpt URLs separately with real services.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
