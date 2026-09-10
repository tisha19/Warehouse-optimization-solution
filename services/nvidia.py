"""NVIDIA NIM, cuOpt, and Guardrails clients."""

import json
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
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.fallback: Optional[JsonHttpClient] = None
        if config.nim_cloud_fallback and config.nim_cloud_api_key and config.nim_cloud_base_url.rstrip("/") != self.client.base_url:
            self.fallback = JsonHttpClient(config.nim_cloud_base_url, config.request_timeout_seconds, config.nim_cloud_api_key)

    @classmethod
    def for_subagent(cls, config: ProductionConfig) -> "NIMClient":
        """Specialists run on their own smaller NIM, which is a separate endpoint."""
        model = config.nim_subagent_model or config.nim_model
        return cls(config, base_url=config.nim_subagent_base_url or config.nim_base_url, model=model, cloud_model=model)

    def _record(self, response: Mapping[str, Any]) -> Dict[str, int]:
        usage = response.get("usage") or {}
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        self.calls += 1
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        return {"prompt_tokens": prompt, "completion_tokens": completion}

    def chat(self, messages: List[Mapping[str, str]], tools: Optional[List[Mapping[str, Any]]] = None, model: Optional[str] = None, json_only: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model or self.model, "messages": messages, "temperature": 0.1}
        if tools:
            payload["tools"] = tools
        if json_only:
            # Nemotron is a reasoning model, so without constrained decoding it
            # answers with chain-of-thought prose instead of the JSON we parse.
            payload["response_format"] = {"type": "json_object"}
            payload["reasoning_effort"] = "none"
        try:
            response = self.client.post("chat/completions", payload)
            self.active_endpoint = self.client.base_url
            self._record(response)
            return response
        except ServiceError:
            if not self.fallback:
                raise
            payload["model"] = self.fallback_model if model is None else model
            response = self.fallback.post("chat/completions", payload)
            self.active_endpoint = self.fallback.base_url
            self._record(response)
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
    """Screens planner input and output through the self-hosted NeMo Guardrails service."""

    def __init__(self, config: ProductionConfig):
        self.client = JsonHttpClient(config.guardrails_url, config.request_timeout_seconds, config.nim_api_key) if config.guardrails_url else None
        self.config_id = config.guardrails_config_id
        self.model = config.nim_model

    def validate(self, stage: str, payload: Mapping[str, Any]) -> PolicyDecision:
        if self.client:
            try:
                result = self.client.post("v1/guardrail/checks", self._request(stage, payload))
            except ServiceError:
                # An unreachable guardrails service must not disable policy enforcement.
                return self._local_baseline(stage, payload)
            status = str(result.get("status", ""))
            if not status:
                return self._local_baseline(stage, payload)
            reason = json.dumps(result.get("rails_status") or {}, sort_keys=True)
            return PolicyDecision(status == "success", f"NeMo Guardrails {status}: {reason}", "nemo-guardrails")
        return self._local_baseline(stage, payload)

    def _request(self, stage: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        # The output rails only fire on an assistant turn, the input rails on a user turn.
        role = "assistant" if stage == "output" else "user"
        return {
            "model": self.model,
            "messages": [{"role": role, "content": json.dumps(payload, default=str)}],
            # The service ignores a top-level config_id and silently runs no rails.
            "guardrails": {"config_id": self.config_id},
        }

    @staticmethod
    def _local_baseline(stage: str, payload: Mapping[str, Any]) -> PolicyDecision:
        if stage == "tool_call" and payload.get("action") == "wms_write" and not payload.get("approval_id"):
            return PolicyDecision(False, "WMS writes require an approval id", "warehouse-write-approval")
        return PolicyDecision(True, "Local baseline policy passed", "local-baseline")
