"""OpenShell Governor — the governed ingress/egress gateway for the deep agent.

This service is the enforcement point of ``deploy/openshell/openshell-policies.yaml``:

* **Gate API** (``POST /api/v1/gate``) — in-process chokepoints (MCP tool
  registry, Postgres connector, SDK egress) ask for a grant before every call.
* **Proxy** (``/proxy/{service}/u/{user}/{upstream}/…``) — LLM (NVIDIA NIM) and
  cuOpt / HTTP egress physically flows through here, so no agent process talks
  to a model or optimizer endpoint directly.
* **Approvals** — with ``approval: per_user`` the first call by a user to a
  service parks as a pending request; the admin approves ONCE for that user in
  the console; the grant persists and every subsequent call is registered in
  the audit log.
* **Admin console** (``/``) — live UI to edit the policy YAML, watch services,
  approve/deny pending requests, revoke grants and tail the audit log.

Run it with:  ``python -m services.openshell.governor``  (default port 8811).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.openshell.policy import (
    GovernancePolicy,
    PolicyError,
    default_policy_path,
    load_policy,
    parse_policy,
)

log = logging.getLogger("OpenShellGovernor")

AUDIT_MEMORY_LIMIT = 1000
# Hop-by-hop / recomputed headers that must not be forwarded through the proxy.
_SKIP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_SKIP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_dir() -> Path:
    configured = os.getenv("OPENSHELL_STATE_DIR", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(__file__).resolve().parents[2] / "deploy/openshell/state"
    path.mkdir(parents=True, exist_ok=True)
    return path


class GovernorState:
    """Policy + grants + pending requests + audit log, persisted as small files."""

    def __init__(self, policy_path: Path | None = None, state_dir: Path | None = None):
        self.policy_path = policy_path or default_policy_path()
        self.state_dir = state_dir or _state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.policy: GovernancePolicy = load_policy(self.policy_path)
        self.grants: dict[str, dict[str, Any]] = self._load_json("grants.json")
        self.requests: dict[str, dict[str, Any]] = self._load_json("requests.json")
        self.audit: list[dict[str, Any]] = []
        self.lock = asyncio.Lock()
        self.approval_changed = asyncio.Condition()

    # ------------------------------------------------------------------ storage
    def _load_json(self, name: str) -> dict[str, Any]:
        path = self.state_dir / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # corrupted state must not brick governance
            log.warning("Could not load %s (%s); starting empty.", path, exc)
            return {}

    def _save_json(self, name: str, data: dict[str, Any]) -> None:
        (self.state_dir / name).write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )

    def save_grants(self) -> None:
        self._save_json("grants.json", self.grants)

    def save_requests(self) -> None:
        self._save_json("requests.json", self.requests)

    # ------------------------------------------------------------------ audit
    def register(self, entry: dict[str, Any]) -> None:
        entry.setdefault("ts", _utcnow())
        self.audit.append(entry)
        if len(self.audit) > AUDIT_MEMORY_LIMIT:
            del self.audit[: len(self.audit) - AUDIT_MEMORY_LIMIT]
        try:
            with (self.state_dir / "audit.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except Exception:  # audit persistence must never break a call
            log.exception("Failed to persist audit entry")

    # ------------------------------------------------------------------ grants
    @staticmethod
    def grant_key(user: str, service: str) -> str:
        return f"{user}::{service}"

    def find_grant(self, user: str, service: str) -> dict[str, Any] | None:
        return self.grants.get(self.grant_key(user, service))

    def add_grant(
        self, user: str, service: str, *, granted_by: str, status: str = "approved",
        scope: str = "call",
    ) -> dict[str, Any]:
        grant = {
            "user": user,
            "service": service,
            "status": status,  # approved | denied
            "scope": scope,  # call | session
            "granted_by": granted_by,
            "granted_at": _utcnow(),
            "calls": 0,
        }
        self.grants[self.grant_key(user, service)] = grant
        self.save_grants()
        return grant

    def count_call(self, user: str, service: str) -> None:
        grant = self.find_grant(user, service)
        if grant is not None:
            grant["calls"] = int(grant.get("calls", 0)) + 1
            self.save_grants()

    # ------------------------------------------------------------------ requests
    def find_pending_request(self, user: str, service: str) -> dict[str, Any] | None:
        for req in self.requests.values():
            if (
                req.get("user") == user
                and req.get("service") == service
                and req.get("status") == "pending"
            ):
                return req
        return None

    def create_request(self, user: str, service: str, operation: str) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:12]
        req = {
            "id": request_id,
            "user": user,
            "service": service,
            "first_operation": operation,
            "status": "pending",  # pending | approved | denied
            "requested_at": _utcnow(),
            "resolved_at": None,
            "resolved_by": None,
        }
        self.requests[request_id] = req
        self.save_requests()
        return req


state: GovernorState | None = None
_http_client: httpx.AsyncClient | None = None


def get_state() -> GovernorState:
    if state is None:  # pragma: no cover - guarded by app lifespan
        raise RuntimeError("Governor state not initialized")
    return state


# ---------------------------------------------------------------------- gate
async def evaluate_gate(
    st: GovernorState,
    *,
    user: str,
    service: str,
    operation: str,
    wait_seconds: float | None,
    source: str,
) -> dict[str, Any]:
    """Decide allowed/pending/denied for one call, creating a pending request
    (and optionally waiting for the admin) on first use of a per_user service.

    ``per_call`` services take this further: every explicit gate announcement
    (``source == "gate"`` — e.g. the app declaring "a new LLM run/chat is
    starting") parks a FRESH pending request and waits for the admin, even if
    the user already holds an approved grant from an earlier call and the
    upstream (build.nvidia.com) is already registered. The approval still
    writes a grant so the streamed HTTP traffic of that approved call flows
    through the proxy (``source == "proxy"``) without an admin click per
    token request — and every one of those requests stays in the audit log."""
    svc = st.policy.service(service)
    if svc is None:
        decision = "denied" if st.policy.default_action == "deny" else "allowed"
        st.register(
            {
                "user": user,
                "service": service,
                "operation": operation,
                "decision": decision,
                "source": source,
                "note": "service not declared in policy",
            }
        )
        return {
            "decision": decision,
            "reason": f"service '{service}' is not declared in the OpenShell policy",
        }

    async def _allowed(note: str | None = None) -> dict[str, Any]:
        st.count_call(user, service)
        st.register(
            {
                "user": user,
                "service": service,
                "operation": operation,
                "decision": "allowed",
                "source": source,
                **({"note": note} if note else {}),
            }
        )
        return {"decision": "allowed"}

    if svc.approval == "auto":
        async with st.lock:
            if st.find_grant(user, service) is None:
                st.add_grant(user, service, granted_by="policy:auto")
        return await _allowed("auto-approved by policy")

    # per_call + an explicit gate announcement: ignore any standing grant
    # (approved OR denied) and park a fresh request for the admin each time.
    # A session-scoped approval is the admin deliberately opting out of that.
    grant = st.find_grant(user, service)
    session_cleared = grant is not None and grant.get("status") == "approved" and grant.get("scope") in ("session", "always")
    fresh_each_call = svc.approval == "per_call" and source == "gate" and not session_cleared
    if not fresh_each_call and grant is not None:
        if grant.get("status") == "approved":
            return await _allowed()
        st.register(
            {
                "user": user,
                "service": service,
                "operation": operation,
                "decision": "denied",
                "source": source,
                "note": "user was denied for this service",
            }
        )
        return {
            "decision": "denied",
            "reason": f"user '{user}' was denied access to '{service}' by the admin",
        }

    async with st.lock:
        req = st.find_pending_request(user, service)
        if req is None:
            req = st.create_request(user, service, operation)
            log.info(
                "New approval request %s: user=%s service=%s (operation=%s)",
                req["id"],
                user,
                service,
                operation,
            )

    # Long-poll for the admin's one-time approval.
    max_wait = st.policy.approval_wait_seconds if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + max(0.0, float(max_wait))
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            async with st.approval_changed:
                await asyncio.wait_for(st.approval_changed.wait(), timeout=min(remaining, 5.0))
        except asyncio.TimeoutError:
            pass
        if fresh_each_call:
            # A standing grant proves nothing for per_call — only THIS request's
            # resolution counts.
            current = st.requests.get(req["id"]) or {}
            if current.get("status") == "approved":
                return await _allowed(
                    f"approved by {current.get('resolved_by')} (per_call)"
                )
            if current.get("status") == "denied":
                return {
                    "decision": "denied",
                    "reason": (
                        f"this call to '{service}' was denied for user '{user}' "
                        "by the admin"
                    ),
                }
            continue
        grant = st.find_grant(user, service)
        if grant is not None and grant.get("status") == "approved":
            return await _allowed(f"approved by {grant.get('granted_by')}")
        if grant is not None and grant.get("status") == "denied":
            return {
                "decision": "denied",
                "reason": f"user '{user}' was denied access to '{service}' by the admin",
            }

    st.register(
        {
            "user": user,
            "service": service,
            "operation": operation,
            "decision": "pending",
            "source": source,
            "request_id": req["id"],
        }
    )
    return {
        "decision": "pending",
        "request_id": req["id"],
        "reason": (
            (
                f"every call to '{service}' requires admin approval (per_call policy) — "
                if fresh_each_call
                else f"first use of '{service}' by '{user}' requires a one-time admin approval — "
            )
            + f"pending request {req['id']} in the OpenShell console"
        ),
    }


# ---------------------------------------------------------------------- app
app = FastAPI(title="EY OpenShell Governor", version="1.0.0")


@app.on_event("startup")
async def _startup() -> None:
    global state, _http_client
    try:  # corporate TLS interception (same rationale as services/agents/llm.py)
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass
    if state is None:
        state = GovernorState()
    _http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    log.info(
        "OpenShell Governor up: policy=%s services=%s",
        state.policy_path,
        ", ".join(state.policy.services) or "(none)",
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _http_client is not None:
        await _http_client.aclose()


class GateRequest(BaseModel):
    user: str = Field(..., min_length=1)
    service: str = Field(..., min_length=1)
    operation: str = Field(default="call")
    wait_seconds: float | None = Field(default=None, ge=0, le=600)


class ResolveRequest(BaseModel):
    resolved_by: str = Field(default="admin")
    reason: str | None = None
    # "call" resolves this request alone; "session" stands until the dataset
    # changes; "always" survives that too. Only the latter two stop a per_call
    # service asking on every call.
    scope: str = Field(default="call")


class PolicyUpdate(BaseModel):
    yaml: str


class GrantRequest(BaseModel):
    user: str
    service: str
    granted_by: str = Field(default="admin")
    scope: str = Field(default="session")


@app.get("/api/v1/healthz")
async def healthz() -> dict[str, Any]:
    st = get_state()
    pending = sum(1 for r in st.requests.values() if r.get("status") == "pending")
    return {
        "status": "ok",
        "policy_file": str(st.policy_path),
        "services": len(st.policy.services),
        "pending_requests": pending,
    }


@app.post("/api/v1/gate")
async def gate(payload: GateRequest) -> dict[str, Any]:
    st = get_state()
    return await evaluate_gate(
        st,
        user=payload.user,
        service=payload.service,
        operation=payload.operation,
        wait_seconds=payload.wait_seconds,
        source="gate",
    )


@app.get("/api/v1/services")
async def list_services() -> list[dict[str, Any]]:
    st = get_state()
    out = []
    for name, svc in st.policy.services.items():
        grants = [g for g in st.grants.values() if g["service"] == name]
        out.append(
            {
                "name": name,
                "kind": svc.kind,
                "description": svc.description,
                "approval": svc.approval,
                "upstreams": svc.upstreams,
                "approved_users": [
                    g["user"] for g in grants if g.get("status") == "approved"
                ],
                "denied_users": [g["user"] for g in grants if g.get("status") == "denied"],
                "calls": sum(int(g.get("calls", 0)) for g in grants),
                "pending": [
                    r["user"]
                    for r in st.requests.values()
                    if r.get("service") == name and r.get("status") == "pending"
                ],
            }
        )
    return out


class UpstreamUpdate(BaseModel):
    url: str = Field(..., min_length=1)


@app.put("/api/v1/services/{service}/upstreams/{name}")
async def put_service_upstream(service: str, name: str, payload: UpstreamUpdate) -> dict[str, Any]:
    """Register/refresh a named upstream for a service at runtime (e.g. the
    UI-selected self-hosted LLM endpoint). Runtime upstreams live in the
    governor's in-memory policy (the YAML file keeps its comments); they show
    up in the console and reset on governor restart — the app re-registers
    them whenever its LLM config is applied."""
    st = get_state()
    svc = st.policy.service(service)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"unknown service '{service}'")
    url = payload.url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="upstream must be an http(s) URL")
    previous = svc.upstreams.get(name)
    svc.upstreams[name] = url
    st.register(
        {
            "user": "app",
            "service": service,
            "operation": f"upstream:{name}",
            "decision": "registered",
            "source": "admin",
            "note": f"{previous or '(new)'} -> {url}",
        }
    )
    return {"status": "registered", "service": service, "upstreams": svc.upstreams}


@app.get("/api/v1/requests")
async def list_requests(status: str | None = None) -> list[dict[str, Any]]:
    st = get_state()
    requests = list(st.requests.values())
    if status:
        requests = [r for r in requests if r.get("status") == status]
    return sorted(requests, key=lambda r: r.get("requested_at", ""), reverse=True)


async def _resolve_request(request_id: str, *, approve: bool, payload: ResolveRequest):
    st = get_state()
    req = st.requests.get(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"unknown request {request_id}")
    if req.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"request already {req.get('status')}")
    req["status"] = "approved" if approve else "denied"
    req["resolved_at"] = _utcnow()
    req["resolved_by"] = payload.resolved_by
    if payload.reason:
        req["reason"] = payload.reason
    st.save_requests()
    st.add_grant(
        req["user"],
        req["service"],
        granted_by=payload.resolved_by,
        status="approved" if approve else "denied",
        scope=payload.scope if approve else "call",
    )
    st.register(
        {
            "user": req["user"],
            "service": req["service"],
            "operation": "approval",
            "decision": req["status"],
            "source": "admin",
            "request_id": request_id,
            "resolved_by": payload.resolved_by,
        }
    )
    async with st.approval_changed:
        st.approval_changed.notify_all()
    return req


@app.post("/api/v1/requests/{request_id}/approve")
async def approve_request(request_id: str, payload: ResolveRequest) -> dict[str, Any]:
    return await _resolve_request(request_id, approve=True, payload=payload)


@app.post("/api/v1/requests/{request_id}/deny")
async def deny_request(request_id: str, payload: ResolveRequest) -> dict[str, Any]:
    return await _resolve_request(request_id, approve=False, payload=payload)


@app.get("/api/v1/grants")
async def list_grants() -> list[dict[str, Any]]:
    st = get_state()
    return sorted(
        st.grants.values(), key=lambda g: g.get("granted_at", ""), reverse=True
    )


@app.post("/api/v1/grants")
async def create_grant(payload: GrantRequest) -> dict[str, Any]:
    """Clear a service before it is asked for.

    An agent only requests the next service once the previous one is allowed,
    so a queue of them never appears for the admin to approve in one go. This
    is how a run is authorised up front instead of gate by gate.
    """
    st = get_state()
    scope = payload.scope if payload.scope in ("call", "session", "always") else "session"
    async with st.lock:
        grant = st.add_grant(
            payload.user, payload.service, granted_by=payload.granted_by, scope=scope
        )
        # A run already parked on this service should resume, not keep waiting.
        for req in st.requests.values():
            if (
                req.get("status") == "pending"
                and req.get("user") == payload.user
                and req.get("service") == payload.service
            ):
                req["status"] = "approved"
                req["resolved_at"] = _utcnow()
                req["resolved_by"] = payload.granted_by
                req["reason"] = f"pre-approved ({scope})"
        st.save_requests()
    st.register(
        {
            "user": payload.user,
            "service": payload.service,
            "operation": "pre_approval",
            "decision": "approved",
            "source": "admin",
            "note": f"granted up front ({scope})",
        }
    )
    async with st.approval_changed:
        st.approval_changed.notify_all()
    return grant


@app.delete("/api/v1/grants")
async def revoke_all_grants(keep_always: bool = False) -> dict[str, Any]:
    """Admin reset: revoke every grant at once (approved and denied alike).

    Every user goes back through the approval flow on their next gated call.
    In-flight pending requests are also cleared so stale approvals cannot
    linger past the reset. ``keep_always`` spares grants the admin marked as
    standing, which is what a change of dataset wants rather than a full reset."""
    st = get_state()
    if keep_always:
        kept = {k: g for k, g in st.grants.items() if g.get("scope") == "always"}
        revoked = len(st.grants) - len(kept)
        st.grants = kept
    else:
        revoked = len(st.grants)
        st.grants.clear()
    st.save_grants()
    cleared_pending = 0
    for req in st.requests.values():
        if req.get("status") == "pending":
            req["status"] = "denied"
            req["resolved_at"] = _utcnow()
            req["resolved_by"] = "admin:revoke_all"
            req["reason"] = "cleared by revoke-all reset"
            cleared_pending += 1
    if cleared_pending:
        st.save_requests()
    st.register(
        {
            "user": "*",
            "service": "*",
            "operation": "revoke_all",
            "decision": "revoked",
            "source": "admin",
            "note": f"{revoked} grants revoked, {cleared_pending} pending requests cleared",
        }
    )
    async with st.approval_changed:
        st.approval_changed.notify_all()
    return {"status": "revoked", "grants": revoked, "pending_cleared": cleared_pending}


@app.delete("/api/v1/grants/{user}/{service}")
async def revoke_grant(user: str, service: str) -> dict[str, Any]:
    st = get_state()
    key = st.grant_key(user, service)
    grant = st.grants.pop(key, None)
    if grant is None:
        raise HTTPException(status_code=404, detail="no such grant")
    st.save_grants()
    st.register(
        {
            "user": user,
            "service": service,
            "operation": "revoke",
            "decision": "revoked",
            "source": "admin",
        }
    )
    return {"status": "revoked", "grant": grant}


@app.get("/api/v1/audit")
async def audit_log(
    limit: int = 200, service: str | None = None, user: str | None = None
) -> list[dict[str, Any]]:
    st = get_state()
    entries = st.audit
    if service:
        entries = [e for e in entries if e.get("service") == service]
    if user:
        entries = [e for e in entries if e.get("user") == user]
    return list(reversed(entries[-max(1, min(limit, AUDIT_MEMORY_LIMIT)) :]))


@app.get("/api/v1/policy")
async def get_policy() -> dict[str, Any]:
    st = get_state()
    return {
        "path": str(st.policy_path),
        "yaml": st.policy_path.read_text(encoding="utf-8"),
        "services": list(st.policy.services),
        "default": st.policy.default_action,
        "approval_mode": st.policy.approval_mode,
        "wait_seconds": st.policy.approval_wait_seconds,
        "sandbox": st.policy.sandbox,
    }


@app.put("/api/v1/policy")
async def put_policy(payload: PolicyUpdate) -> dict[str, Any]:
    st = get_state()
    try:
        policy = parse_policy(payload.yaml)
    except PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    backup = st.policy_path.with_suffix(st.policy_path.suffix + ".bak")
    try:
        backup.write_text(st.policy_path.read_text(encoding="utf-8"), encoding="utf-8")
    except FileNotFoundError:
        pass
    st.policy_path.write_text(payload.yaml, encoding="utf-8")
    st.policy = policy
    st.register(
        {
            "user": "admin",
            "service": "policy",
            "operation": "update",
            "decision": "applied",
            "source": "admin",
        }
    )
    async with st.approval_changed:
        st.approval_changed.notify_all()
    return {"status": "applied", "services": list(policy.services)}


# ---------------------------------------------------------------------- proxy
@app.api_route(
    "/proxy/{service}/u/{user}/{upstream}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(service: str, user: str, upstream: str, path: str, request: Request):
    """Governed reverse proxy: gate the (user, service) pair, then stream the
    call to the upstream declared in the policy YAML and register it."""
    st = get_state()
    svc = st.policy.service(service)
    if svc is None or not svc.proxied:
        raise HTTPException(
            status_code=403,
            detail=f"'{service}' is not a proxied service in the OpenShell policy",
        )
    base = svc.upstreams.get(upstream)
    if not base:
        raise HTTPException(
            status_code=403,
            detail=f"service '{service}' has no upstream named '{upstream}'",
        )

    operation = f"{request.method} /{path}"
    verdict = await evaluate_gate(
        st,
        user=user,
        service=service,
        operation=operation,
        wait_seconds=None,
        source="proxy",
    )
    if verdict["decision"] != "allowed":
        return JSONResponse(
            status_code=403,
            content={
                "error": "openshell_egress_blocked",
                "decision": verdict["decision"],
                "detail": verdict.get("reason", "blocked by OpenShell policy"),
                "request_id": verdict.get("request_id"),
            },
        )

    target = f"{base}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _SKIP_REQUEST_HEADERS
    }
    body = await request.body()

    assert _http_client is not None
    started = time.monotonic()
    upstream_request = _http_client.build_request(
        request.method, target, headers=headers, content=body
    )
    try:
        upstream_response = await _http_client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        st.register(
            {
                "user": user,
                "service": service,
                "operation": operation,
                "decision": "upstream_error",
                "source": "proxy",
                "note": str(exc),
            }
        )
        raise HTTPException(status_code=502, detail=f"upstream error: {exc}")

    async def _stream():
        try:
            try:
                # Decoded bytes (content-encoding is stripped from the
                # forwarded headers); SSE/token streams pass through live.
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk
            except httpx.StreamConsumed:
                # Response body was already loaded (e.g. mock/intercepted
                # transports) — forward it whole.
                yield upstream_response.content
        finally:
            await upstream_response.aclose()
            st.register(
                {
                    "user": user,
                    "service": service,
                    "operation": operation,
                    "decision": "completed",
                    "source": "proxy",
                    "upstream": upstream,
                    "status_code": upstream_response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                }
            )

    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in _SKIP_RESPONSE_HEADERS
    }
    return StreamingResponse(
        _stream(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )


# ---------------------------------------------------------------------- UI
@app.get("/", response_class=HTMLResponse)
@app.get("/admin", response_class=HTMLResponse)
async def admin_console() -> HTMLResponse:
    html = (Path(__file__).parent / "static/admin.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        app,
        host=os.getenv("OPENSHELL_GOVERNOR_HOST", "0.0.0.0"),
        port=int(os.getenv("OPENSHELL_GOVERNOR_PORT", "8811")),
    )
