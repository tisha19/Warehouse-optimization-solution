"""Policy model for the OpenShell Governor.

Loads/validates ``deploy/openshell/openshell-policies.yaml`` — the single YAML
file that declares every governed egress service (LLM endpoints, cuOpt,
Postgres, the MCP tool registry, …), how each is approved
(``per_user`` → one-time admin approval per user, ``per_call`` → a FRESH admin
approval every time the app announces a new use of the service via the gate
API, even if the user/upstream was approved before, ``auto`` → allowed and
registered) and where its proxied upstreams live.

``${VAR}`` / ``${VAR:-default}`` in scalar values are expanded from the
environment at load time so the same policy file works across environments.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("OpenShellPolicy")

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")

VALID_APPROVALS = ("per_user", "per_call", "auto")


class PolicyError(ValueError):
    """Raised when the policy YAML is structurally invalid."""


@dataclass
class ServicePolicy:
    name: str
    kind: str = "http"
    description: str = ""
    approval: str = "per_user"
    upstreams: dict[str, str] = field(default_factory=dict)

    @property
    def proxied(self) -> bool:
        return bool(self.upstreams)


@dataclass
class GovernancePolicy:
    version: int = 2
    default_action: str = "deny"
    approval_mode: str = "one_time_per_user"
    approval_wait_seconds: float = 90.0
    sandbox: dict[str, Any] = field(default_factory=dict)
    services: dict[str, ServicePolicy] = field(default_factory=dict)
    raw_yaml: str = ""

    def service(self, name: str) -> ServicePolicy | None:
        return self.services.get(name)


def _expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` in strings (recursively in containers)."""
    if isinstance(value, str):
        def _sub(match: re.Match) -> str:
            name = match.group("name")
            default = match.group("default")
            return os.getenv(name) or (default if default is not None else "")

        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def parse_policy(text: str) -> GovernancePolicy:
    """Parse + validate policy YAML text. Raises ``PolicyError`` on bad input."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError("policy YAML must be a mapping at the top level")
    data = _expand_env(data)

    egress = data.get("egress") or {}
    if not isinstance(egress, dict):
        raise PolicyError("`egress` must be a mapping")
    approval = egress.get("approval") or {}

    services_raw = data.get("services") or {}
    if not isinstance(services_raw, dict):
        raise PolicyError("`services` must be a mapping of service name -> spec")

    services: dict[str, ServicePolicy] = {}
    for name, spec in services_raw.items():
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise PolicyError(f"service `{name}` must be a mapping")
        approval_mode = str(spec.get("approval", "per_user")).strip()
        if approval_mode not in VALID_APPROVALS:
            raise PolicyError(
                f"service `{name}`: approval must be one of {VALID_APPROVALS}, "
                f"got {approval_mode!r}"
            )
        upstreams = spec.get("upstreams") or {}
        if not isinstance(upstreams, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in upstreams.items()
        ):
            raise PolicyError(f"service `{name}`: upstreams must map name -> URL")
        for key, url in upstreams.items():
            if url and not url.startswith(("http://", "https://")):
                raise PolicyError(
                    f"service `{name}`: upstream `{key}` must be an http(s) URL, got {url!r}"
                )
        services[name] = ServicePolicy(
            name=name,
            kind=str(spec.get("kind", "http")),
            description=str(spec.get("description", "")).strip(),
            approval=approval_mode,
            upstreams={k: v.rstrip("/") for k, v in upstreams.items()},
        )

    try:
        wait_seconds = float(approval.get("wait_seconds", 90))
    except (TypeError, ValueError) as exc:
        raise PolicyError("egress.approval.wait_seconds must be a number") from exc

    return GovernancePolicy(
        version=int(data.get("version", 2)),
        default_action=str(egress.get("default", "deny")),
        approval_mode=str(approval.get("mode", "one_time_per_user")),
        approval_wait_seconds=wait_seconds,
        sandbox=data.get("sandbox") or {},
        services=services,
        raw_yaml=text,
    )


def default_policy_path() -> Path:
    configured = os.getenv("OPENSHELL_POLICY_FILE", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "deploy/openshell/openshell-policies.yaml"


def load_policy(path: Path | None = None) -> GovernancePolicy:
    """Load and validate the policy file from disk."""
    policy_path = path or default_policy_path()
    text = policy_path.read_text(encoding="utf-8")
    policy = parse_policy(text)
    log.info(
        "Loaded OpenShell policy %s (%d services, default=%s, approval=%s)",
        policy_path,
        len(policy.services),
        policy.default_action,
        policy.approval_mode,
    )
    return policy
