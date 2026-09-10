"""Nemotron-backed demand, inventory, and warehouse specialist agents."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, Optional

from agents.parsing import json_from_response
from services.nvidia import NIMClient

SPECIALIST_BRIEF = {
    "demand": "You assess demand: which lines are accelerating, which are promotional, and where the volume concentrates.",
    "inventory": "You assess inventory: cover, replenishment pressure, and lines at risk of running dry in the forward pick face.",
    "warehouse": "You assess the physical operation: travel, congestion, slot capacity and handling constraints.",
}


class NemotronSpecialistRunner:
    def __init__(self, nim: NIMClient, agent_names: Iterable[str] = ("demand", "inventory", "warehouse"), on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.nim = nim
        self.agent_names = tuple(agent_names)
        self.on_event = on_event

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(kind, payload)

    def _run_one(self, agent_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._emit("specialist_started", {"agent": agent_name})
        brief = SPECIALIST_BRIEF.get(agent_name, f"You are the {agent_name} warehouse specialist.")
        started = time.perf_counter()
        response = self.nim.chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"{brief} You are given an aggregated warehouse summary, not raw records. "
                        'Return only JSON shaped as {"reasoning": ["step", ...], "findings": ["..."], '
                        '"risks": ["..."], "constraints": ["..."]}. Keep reasoning to at most four short steps '
                        "that state what you looked at and what you concluded."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            json_only=True,
        )
        analysis = json_from_response(response, f"response for {agent_name}")
        usage = response.get("usage") or {}
        analysis["telemetry"] = {
            "model": response.get("model", self.nim.model),
            "endpoint": self.nim.active_endpoint,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        }
        self._emit("specialist_finished", {"agent": agent_name, "analysis": analysis})
        return analysis

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"goal": state.get("business_goal"), "warehouse_summary": state.get("data_digest", {}), "policies": state.get("policy_documents", [])}
        # The specialists are independent, so one round trip instead of three.
        with ThreadPoolExecutor(max_workers=len(self.agent_names)) as pool:
            futures = {name: pool.submit(self._run_one, name, payload) for name in self.agent_names}
            return {name: future.result() for name, future in futures.items()}
