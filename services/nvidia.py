"""NVIDIA NIM, cuOpt, and Guardrails clients."""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from services.config import ProductionConfig
from services.http_client import JsonHttpClient, ServiceError


class NIMClient:
    """Calls the self-hosted NIM first and fails over to NVIDIA cloud NIM when it is unreachable."""

    def __init__(self, config: ProductionConfig, base_url: str = "", model: str = "", cloud_model: str = ""):
        self.client = JsonHttpClient(base_url or config.nim_base_url, config.request_timeout_seconds, config.nim_api_key)
        self.model = model or config.nim_model
        self.fallback_model = cloud_model or config.nim_cloud_model or self.model
        self.active_endpoint = self.client.base_url
        self.fallback: Optional[JsonHttpClient] = None
        if config.nim_cloud_fallback and config.nim_cloud_api_key and config.nim_cloud_base_url.rstrip("/") != self.client.base_url:
            self.fallback = JsonHttpClient(config.nim_cloud_base_url, config.request_timeout_seconds, config.nim_cloud_api_key)

    @classmethod
    def for_subagent(cls, config: ProductionConfig) -> "NIMClient":
        """Specialists run on their own smaller NIM, which is a separate endpoint."""
        model = config.nim_subagent_model or config.nim_model
        return cls(config, base_url=config.nim_subagent_base_url or config.nim_base_url, model=model, cloud_model=model)

    def chat(self, messages: List[Mapping[str, str]], tools: Optional[List[Mapping[str, Any]]] = None, model: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model or self.model, "messages": messages, "temperature": 0.1}
        if tools:
            payload["tools"] = tools
        try:
            response = self.client.post("chat/completions", payload)
            self.active_endpoint = self.client.base_url
            return response
        except ServiceError:
            if not self.fallback:
                raise
            payload["model"] = self.fallback_model if model is None else model
            response = self.fallback.post("chat/completions", payload)
            self.active_endpoint = self.fallback.base_url
            return response


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
            try:
                result = self.client.post("validate", {"stage": stage, "payload": payload})
                return PolicyDecision(bool(result.get("allowed")), result.get("reason", ""), result.get("policy_id", "nemo-guardrails"))
            except ServiceError:
                # An unreachable guardrails service must not disable policy enforcement.
                return self._local_baseline(stage, payload)
        return self._local_baseline(stage, payload)

    @staticmethod
    def _local_baseline(stage: str, payload: Mapping[str, Any]) -> PolicyDecision:
        if stage == "tool_call" and payload.get("action") == "wms_write" and not payload.get("approval_id"):
            return PolicyDecision(False, "WMS writes require an approval id", "warehouse-write-approval")
        return PolicyDecision(True, "Local baseline policy passed", "local-baseline")
