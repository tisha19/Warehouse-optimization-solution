"""WMS, ERP, and forecasting service adapters."""

from typing import Any, Dict, Iterable, Mapping

from services.http_client import JsonHttpClient


class WMSAdapter:
    def __init__(self, client: JsonHttpClient, dry_run: bool = True):
        self.client = client
        self.dry_run = dry_run

    def snapshot(self) -> Dict[str, Any]:
        return self.client.post("warehouse/snapshot", {})

    def apply_approved_moves(self, moves: Iterable[Mapping[str, Any]], approval_id: str) -> Dict[str, Any]:
        if self.dry_run:
            return {"status": "DRY_RUN", "approval_id": approval_id, "moves": list(moves)}
        return self.client.post("warehouse/moves", {"approval_id": approval_id, "moves": list(moves)})


class ERPAdapter:
    def __init__(self, client: JsonHttpClient):
        self.client = client

    def sku_master(self) -> Dict[str, Any]:
        return self.client.post("erp/sku-master", {})

    def inbound_shipments(self) -> Dict[str, Any]:
        return self.client.post("erp/inbound-shipments", {})


class ForecastAdapter:
    def __init__(self, client: JsonHttpClient):
        self.client = client

    def forecast(self, horizon_days: int = 14) -> Dict[str, Any]:
        return self.client.post("forecast", {"horizon_days": horizon_days})
