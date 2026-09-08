"""Nemotron-backed demand, inventory, and warehouse specialist agents."""

import json
from typing import Any, Dict, Iterable

from services.nvidia import NIMClient


class NemotronSpecialistRunner:
    def __init__(self, nim: NIMClient, agent_names: Iterable[str] = ("demand", "inventory", "warehouse")):
        self.nim = nim
        self.agent_names = tuple(agent_names)

    def __call__(self, state: Dict[str, Any]) -> Dict[str, Any]:
        results = {}
        payload = {"goal": state.get("business_goal"), "source_data": state.get("source_data", {}), "policies": state.get("policy_documents", [])}
        for agent_name in self.agent_names:
            response = self.nim.chat([
                {"role": "system", "content": f"You are the {agent_name} warehouse specialist. Return only valid JSON with findings, risks, and constraints."},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ])
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            try:
                results[agent_name] = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ValueError(f"NIM returned a non-JSON response for {agent_name}") from exc
        return results
