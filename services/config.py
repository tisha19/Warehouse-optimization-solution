"""Configuration for production NVIDIA agentic integrations."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ProductionConfig:
    nim_base_url: str = os.getenv("NIM_BASE_URL", "http://localhost:8000/v1")
    nim_model: str = os.getenv("NIM_MODEL", "nvidia/nemotron")
    nim_api_key: str = os.getenv("NIM_API_KEY", "")
    retriever_url: str = os.getenv("NEMO_RETRIEVER_URL", "")
    cuopt_url: str = os.getenv("CUOPT_URL", "")
    guardrails_url: str = os.getenv("NEMO_GUARDRAILS_URL", "")
    openshell_url: str = os.getenv("OPENSHELL_URL", "")
    approval_store: str = os.getenv("APPROVAL_STORE", "approvals.json")
    request_timeout_seconds: float = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "30"))
    dry_run: bool = os.getenv("AGENT_DRY_RUN", "true").lower() == "true"

    @classmethod
    def from_env(cls) -> "ProductionConfig":
        return cls()

    def require(self, *services: str) -> None:
        missing = [service for service in services if not getattr(self, service)]
        if missing:
            raise RuntimeError("Missing production service configuration: " + ", ".join(missing))
