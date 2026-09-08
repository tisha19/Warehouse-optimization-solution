"""Nemotron-backed demand, inventory, and warehouse specialist agents."""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable

from services.nvidia import NIMClient


class NemotronSpecialistRunner:
    def __init__(self, nim: NIMClient, agent_names: Iterable[str] = ("demand", "inventory", "warehouse")):
        self.nim = nim
        self.agent_names = tuple(agent_names)

    def _run_one(self, agent_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.nim.chat(
            [
                {"role": "system", "content": f"You are the {agent_name} warehouse specialist. Return only valid JSON with findings, risks, and constraints."},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content") or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"NIM returned a non-JSON response for {agent_name}") from exc

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"goal": state.get("business_goal"), "warehouse_summary": state.get("data_digest", {}), "policies": state.get("policy_documents", [])}
        # The specialists are independent, so one round trip instead of three.
        with ThreadPoolExecutor(max_workers=len(self.agent_names)) as pool:
            futures = {name: pool.submit(self._run_one, name, payload) for name in self.agent_names}
            return {name: future.result() for name, future in futures.items()}
