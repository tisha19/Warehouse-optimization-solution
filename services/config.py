"""Configuration for production NVIDIA agentic integrations."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _load_project_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    # The .env file wins over ambient shell variables so stale exports cannot silently
    # shadow it. Set WAREHOUSE_ENV_PRECEDENCE=process when an orchestrator injects config.
    file_wins = os.getenv("WAREHOUSE_ENV_PRECEDENCE", "file").lower() == "file"

    for item in env_path.read_text(encoding="utf-8").splitlines():
        line = item.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        value = value.strip("\"'")
        if file_wins:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    mappings = {
        "NIM_BASE_URL": ("LLM_NIM_URL",),
        "NIM_MODEL": ("LLM_MODEL",),
        "NIM_API_KEY": ("NVIDIA_API_KEY",),
        "CUOPT_URL": ("CUOPT_SELF_HOSTED_URL", "NVIDIA_CUOPT_URL"),
        "NEMO_GUARDRAILS_URL": ("RAIL_API_URL",),
        "OPENSHELL_URL": (),
    }
    for target, aliases in mappings.items():
        if os.getenv(target):
            continue
        for alias in aliases:
            value = os.getenv(alias)
            if value:
                os.environ[target] = value
                break


_load_project_env()


def _env(name: str, default: str = "", aliases: tuple[str, ...] = ()) -> str:
    for key in (name, *aliases):
        value = os.getenv(key)
        if value not in (None, ""):
            return value
    return default


@dataclass(frozen=True)
class ProductionConfig:
    nim_base_url: str = _env("NIM_BASE_URL", "http://localhost:8000/v1", aliases=("LLM_NIM_URL",))
    nim_model: str = _env("NIM_MODEL", "nvidia/nemotron", aliases=("LLM_MODEL",))
    nim_api_key: str = _env("NIM_API_KEY", "", aliases=("NVIDIA_API_KEY",))
    nim_subagent_model: str = _env("NIM_SUBAGENT_MODEL", "", aliases=("LLM_SUBAGENT_MODEL",))
    nim_subagent_base_url: str = _env("NIM_SUBAGENT_BASE_URL", "")
    # The orchestrator runs on NVIDIA's hosted Nemotron 3 Ultra; the specialists
    # run on the Lightning NIM we serve ourselves. Keeping the two sets of
    # credentials apart is what lets those be different endpoints.
    nim_supervisor_base_url: str = _env("NIM_SUPERVISOR_BASE_URL", "")
    nim_supervisor_model: str = _env("NIM_SUPERVISOR_MODEL", "")
    nim_supervisor_api_key: str = _env("NIM_SUPERVISOR_API_KEY", "")
    nim_cloud_base_url: str = _env("NIM_CLOUD_BASE_URL", "https://integrate.api.nvidia.com/v1")
    nim_cloud_api_key: str = _env("NIM_CLOUD_API_KEY", "", aliases=("NVIDIA_API_KEY",))
    nim_cloud_model: str = _env("NIM_CLOUD_MODEL", "")
    nim_cloud_fallback: bool = _env("NIM_CLOUD_FALLBACK", "true").lower() == "true"
    cuopt_url: str = _env("CUOPT_URL", "", aliases=("CUOPT_SELF_HOSTED_URL", "NVIDIA_CUOPT_URL"))
    guardrails_url: str = _env("NEMO_GUARDRAILS_URL", "", aliases=("RAIL_API_URL",))
    guardrails_config_id: str = _env("NEMO_GUARDRAILS_CONFIG_ID", "warehouse")
    openshell_url: str = _env("OPENSHELL_URL", "")
    approval_store: str = _env("APPROVAL_STORE", "approvals.json")
    request_timeout_seconds: float = float(_env("AGENT_REQUEST_TIMEOUT_SECONDS", "30"))
    dry_run: bool = _env("AGENT_DRY_RUN", "true").lower() == "true"

    @classmethod
    def from_env(cls) -> "ProductionConfig":
        return cls()

    def require(self, *services: str) -> None:
        missing = [service for service in services if not getattr(self, service)]
        if missing:
            raise RuntimeError("Missing production service configuration: " + ", ".join(missing))
