"""NVIDIA NIM, NeMo Retriever, cuOpt, and Guardrails clients."""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from services.config import ProductionConfig
from services.http_client import JsonHttpClient


class NIMClient:
    def __init__(self, config: ProductionConfig):
        self.client = JsonHttpClient(config.nim_base_url, config.request_timeout_seconds, config.nim_api_key)
        self.model = config.nim_model

    def chat(self, messages: List[Mapping[str, str]], tools: Optional[List[Mapping[str, Any]]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.1}
        if tools:
            payload["tools"] = tools
        return self.client.post("chat/completions", payload)


class NeMoRetriever:
    def __init__(self, config: ProductionConfig):
        if not config.retriever_url:
            raise RuntimeError("NEMO_RETRIEVER_URL is required for production retrieval")
        self.client = JsonHttpClient(config.retriever_url, config.request_timeout_seconds, config.nim_api_key)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        response = self.client.post("search", {"query": query, "top_k": top_k})
        return list(response.get("documents", response.get("results", [])))


class CuOptClient:
    def __init__(self, config: ProductionConfig):
        if not config.cuopt_url:
            raise RuntimeError("CUOPT_URL is required for production optimization")
        self.client = JsonHttpClient(config.cuopt_url, config.request_timeout_seconds, config.nim_api_key)

    def solve_slotting(self, problem: Mapping[str, Any]) -> Dict[str, Any]:
        return self.client.post("solve/slotting", problem)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    policy_id: str


class GuardrailsClient:
    def __init__(self, config: ProductionConfig):
        self.client = JsonHttpClient(config.guardrails_url, config.request_timeout_seconds, config.nim_api_key) if config.guardrails_url else None

    def validate(self, stage: str, payload: Mapping[str, Any]) -> PolicyDecision:
        if self.client:
            result = self.client.post("validate", {"stage": stage, "payload": payload})
            return PolicyDecision(bool(result.get("allowed")), result.get("reason", ""), result.get("policy_id", "nemo-guardrails"))
        if stage == "tool_call" and payload.get("action") == "wms_write" and not payload.get("approval_id"):
            return PolicyDecision(False, "WMS writes require an approval id", "warehouse-write-approval")
        return PolicyDecision(True, "Local baseline policy passed", "local-baseline")
