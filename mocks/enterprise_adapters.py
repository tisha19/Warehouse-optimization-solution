"""In-process adapters backed by the synthetic enterprise service state."""

from typing import Any, Dict, Iterable, Mapping

from mocks.enterprise_services import MockServiceState


class SyntheticWMSAdapter:
    def __init__(self, state: MockServiceState):
        self.state = state

    def snapshot(self) -> Dict[str, Any]:
        return self.state.snapshot()

    def apply_approved_moves(self, moves: Iterable[Mapping[str, Any]], approval_id: str) -> Dict[str, Any]:
        applied = list(moves)
        relocated = self.state.apply_moves(applied)
        return {"status": "APPLIED", "approval_id": approval_id, "moves": applied, "relocated": relocated}


class SyntheticERPAdapter:
    def __init__(self, state: MockServiceState):
        self.state = state

    def sku_master(self) -> Dict[str, Any]:
        return self.state.sku_master()

    def inbound_shipments(self) -> Dict[str, Any]:
        return self.state.inbound()


class SyntheticForecastAdapter:
    def __init__(self, state: MockServiceState):
        self.state = state

    def forecast(self, horizon_days: int = 14) -> Dict[str, Any]:
        return self.state.forecast(horizon_days)
