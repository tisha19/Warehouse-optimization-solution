"""In-process client for the OpenShell Governor (the app side of governance).

Every egress chokepoint in the application calls into this module instead of
reaching its upstream directly:

* ``services/agents/llm.py``      — builds chat models against the governor's
  LLM proxy (``proxy_base_url``) so NIM traffic flows through OpenShell.
* ``services/agents/tools.py``    — ``MCPToolRegistry.call`` gates every
  warehouse.* tool invocation (``agate('tools.mcp', tool)``).
* ``utils/postgres_utils.py``     — ``PostgresHelper.conn`` gates every
  database connection (``gate('postgres', 'connect')``).
* cuOpt clients / WCS trigger     — gate + route through the governor proxy.

Modes (``OPENSHELL_GOVERNED``):

* ``true``  — governed; if the governor is unreachable every gated call FAILS
  CLOSED with ``EgressDeniedError`` (governance cannot silently disappear).
* ``false`` — ungoverned legacy mode; gates are no-ops, URLs stay direct.
* ``auto``  (default) — probe the governor once at first use: reachable →
  behave like ``true`` for the rest of the process; unreachable → log a
  prominent warning and behave like ``false``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextvars import ContextVar
from urllib.parse import quote, urlsplit

import httpx

log = logging.getLogger("OpenShellGovernance")

GOVERNED_ENV = "OPENSHELL_GOVERNED"
ENABLED_ENV = "OPENSHELL_ENABLED"
GOVERNOR_URL_ENV = "OPENSHELL_GOVERNOR_URL"
DEFAULT_GOVERNOR_URL = "http://localhost:8811"
DEFAULT_USER_ENV = "OPENSHELL_DEFAULT_USER"
APPROVAL_WAIT_ENV = "OPENSHELL_APPROVAL_WAIT_SECONDS"

# Identity of the caller on whose behalf agents are running. Set per HTTP
# request by main.py middleware (X-User-Id header), or explicitly by the
# scheduler for autonomous runs.
current_user: ContextVar[str | None] = ContextVar("openshell_current_user", default=None)

_mode_lock = threading.Lock()
_resolved_governed: bool | None = None
_sync_client: httpx.Client | None = None


class EgressDeniedError(RuntimeError):
    """An egress call was blocked by the OpenShell governance policy."""


def set_current_user(user: str | None):
    """Bind the acting user for this context; returns the reset token."""
    return current_user.set(user)


def get_current_user() -> str:
    user = current_user.get()
    if user:
        return user
    return os.getenv(DEFAULT_USER_ENV, "warehouse-user").strip() or "warehouse-user"


def openshell_enabled() -> bool:
    """Master switch (``OPENSHELL_ENABLED``, default false). When off, OpenShell
    is turned off entirely — no egress governance and no sandbox backend — so the
    deep agent flow runs without OpenShell (and without needing Docker)."""
    return os.getenv(ENABLED_ENV, "false").strip().lower() in ("true", "1", "on", "yes")


def governor_url() -> str:
    return (os.getenv(GOVERNOR_URL_ENV, DEFAULT_GOVERNOR_URL).strip() or DEFAULT_GOVERNOR_URL).rstrip("/")


def _approval_wait_seconds() -> float:
    try:
        return float(os.getenv(APPROVAL_WAIT_ENV, "120"))
    except ValueError:
        return 120.0


def _client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        # Generous read timeout: gate() long-polls while the admin approves.
        _sync_client = httpx.Client(timeout=httpx.Timeout(30.0, read=630.0, connect=5.0))
    return _sync_client


def _probe_governor() -> bool:
    try:
        response = _client().get(f"{governor_url()}/api/v1/healthz")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def governed() -> bool:
    """Whether egress governance is active for this process (see module doc)."""
    global _resolved_governed
    # Master switch: OpenShell off entirely unless OPENSHELL_ENABLED is set.
    if not openshell_enabled():
        return False
    mode = os.getenv(GOVERNED_ENV, "auto").strip().lower()
    if mode in ("false", "0", "off", "no"):
        return False
    if mode in ("true", "1", "on", "yes"):
        return True
    # auto: probe once, then cache for the lifetime of the process.
    if _resolved_governed is None:
        with _mode_lock:
            if _resolved_governed is None:
                reachable = _probe_governor()
                _resolved_governed = reachable
                if reachable:
                    log.info(
                        "OpenShell Governor reachable at %s — egress governance ACTIVE.",
                        governor_url(),
                    )
                else:
                    log.warning(
                        "OpenShell Governor NOT reachable at %s — running UNGOVERNED "
                        "(set OPENSHELL_GOVERNED=true to fail closed instead).",
                        governor_url(),
                    )
    return _resolved_governed


def reset_mode_cache() -> None:
    """Testing hook: forget the cached auto-mode probe result."""
    global _resolved_governed
    _resolved_governed = None


def _raise_for_verdict(verdict: dict, service: str, user: str) -> None:
    decision = verdict.get("decision")
    if decision == "allowed":
        return
    raise EgressDeniedError(
        verdict.get("reason")
        or f"OpenShell policy blocked '{service}' for user '{user}' ({decision})"
    )


def gate(service: str, operation: str = "call", *, user: str | None = None) -> None:
    """Synchronous gate: no-op when ungoverned; raises ``EgressDeniedError``
    when the governor denies (or approval stays pending past the wait)."""
    if not governed():
        return
    acting_user = user or get_current_user()
    payload = {
        "user": acting_user,
        "service": service,
        "operation": operation,
        "wait_seconds": _approval_wait_seconds(),
    }
    try:
        response = _client().post(f"{governor_url()}/api/v1/gate", json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise EgressDeniedError(
            f"OpenShell Governor unreachable while gating '{service}' "
            f"({exc}); failing closed"
        ) from exc
    _raise_for_verdict(response.json(), service, acting_user)


async def agate(service: str, operation: str = "call", *, user: str | None = None) -> None:
    """Async variant of :func:`gate` (used on the orchestrator's event loop)."""
    if not governed():
        return
    await asyncio.to_thread(gate, service, operation, user=user)


def proxy_base_url(service: str, upstream: str = "default", *, user: str | None = None) -> str:
    """Base URL that routes a client's HTTP calls through the governor proxy.

    e.g. ``proxy_base_url('llm.supervisor') + '/v1'`` is a drop-in replacement
    for ``https://integrate.api.nvidia.com/v1`` — same path shape downstream,
    but every request is gated, per-user, and registered in the audit log.
    """
    acting_user = quote(user or get_current_user(), safe="")
    return f"{governor_url()}/proxy/{quote(service, safe='')}/u/{acting_user}/{quote(upstream, safe='')}"


def register_upstream(service: str, name: str, url: str) -> None:
    """Declare/refresh a named upstream for a governed service (e.g. a
    self-hosted LLM endpoint chosen at runtime in the UI).

    Only the ORIGIN of ``url`` is registered — the governor resolves proxied
    paths against it. The upstream becomes visible in the admin console and is
    still subject to the service's per-user approval. No-op when ungoverned;
    fails closed when the governor is unreachable.
    """
    if not governed():
        return
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        response = _client().put(
            f"{governor_url()}/api/v1/services/{quote(service, safe='')}/upstreams/{quote(name, safe='')}",
            json={"url": origin},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise EgressDeniedError(
            f"could not register upstream '{name}' for '{service}' with the "
            f"OpenShell governor ({exc})"
        ) from exc


def governed_url(url: str, service: str, upstream: str = "default", *, user: str | None = None) -> str:
    """Rewrite a direct upstream URL into its governed proxy equivalent.

    Path and query are preserved; the upstream ORIGIN is resolved by the
    governor from the policy YAML (``services.<service>.upstreams.<upstream>``),
    so the policy — not app env vars — decides where governed traffic goes.
    Returns ``url`` unchanged when governance is off.
    """
    if not governed():
        return url
    parts = urlsplit(url)
    query = f"?{parts.query}" if parts.query else ""
    return f"{proxy_base_url(service, upstream, user=user)}{parts.path}{query}"
