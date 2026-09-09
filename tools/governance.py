"""Approval persistence and OpenShell permission enforcement."""

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

from services.config import ProductionConfig
from services.nvidia import PolicyDecision

LOG = logging.getLogger("governance")


class OpenShellPolicy:
    """Asks the OpenShell Governor for a grant, backed by a local allowlist."""

    def __init__(self, config: ProductionConfig):
        self.endpoint = config.openshell_url.rstrip("/")
        self.timeout = config.request_timeout_seconds
        self.allowed_tools = {"read_wms", "read_erp", "read_forecast", "load_policy", "llm.supervisor", "llm.subagent", "solve_slotting", "create_approval", "write_wms"}

    def authorize(self, tool_name: str, actor: str, approval_id: str = "", wait_seconds: float = 0) -> PolicyDecision:
        local = self._local_allowlist(tool_name, approval_id, actor)
        # The local allowlist is a hard floor: the governor may only narrow it.
        if not local.allowed or not self.endpoint:
            return local
        return self._gate(tool_name, actor, wait_seconds) or local

    def _local_allowlist(self, tool_name: str, approval_id: str, actor: str) -> PolicyDecision:
        if tool_name not in self.allowed_tools:
            return PolicyDecision(False, f"Tool is not allowlisted: {tool_name}", "openshell-tool-allowlist")
        if tool_name == "write_wms" and not approval_id:
            return PolicyDecision(False, "write_wms requires approval", "openshell-write-approval")
        return PolicyDecision(True, f"Authorized for actor {actor}", "openshell-local-policy")

    def _gate(self, tool_name: str, actor: str, wait_seconds: float = 0) -> PolicyDecision | None:
        """Returns None when the governor is unreachable so the local floor applies.

        A per_call service parks a fresh request on every gate call, so retrying
        after an approval never succeeds; such callers pass wait_seconds and let
        the governor hold the call until an operator decides.
        """
        payload = json.dumps({"user": actor, "service": tool_name, "operation": "call", "wait_seconds": wait_seconds}).encode()
        request = urllib.request.Request(
            self.endpoint + "/api/v1/gate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=max(self.timeout, wait_seconds + 30)) as response:
                verdict = json.loads(response.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
            return None
        decision = str(verdict.get("decision", ""))
        reason = str(verdict.get("reason") or f"OpenShell decision: {decision}")
        return PolicyDecision(decision == "allowed", reason, "openshell-governor")


class ApprovalWorkflow:
    # The UI plans on a background thread while requests are served, so the
    # read-modify-write below has to be serialised or updates are lost.
    _lock = threading.Lock()

    def __init__(self, store_path: str):
        self.path = Path(store_path)

    def create(self, moves: List[Mapping[str, Any]], requested_by: str, validation: Mapping[str, Any]) -> Dict[str, Any]:
        raw = json.dumps({"moves": moves, "requested_by": requested_by, "validation": validation}, sort_keys=True)
        approval = {"approval_id": "APR-" + hashlib.sha256(raw.encode()).hexdigest()[:12], "status": "PENDING", "requested_by": requested_by, "created_at": datetime.now(timezone.utc).isoformat(), "moves": moves, "validation": validation}
        with self._lock:
            records = self._read()
            records.append(approval)
            self._write(records)
        return approval

    def decide(self, approval_id: str, approver: str, approved: bool, reason: str = "") -> Dict[str, Any]:
        with self._lock:
            records = self._read()
            for record in records:
                if record["approval_id"] == approval_id:
                    record.update({"status": "APPROVED" if approved else "REJECTED", "approver": approver, "decision_at": datetime.now(timezone.utc).isoformat(), "reason": reason})
                    self._write(records)
                    return record
        raise KeyError(f"Approval not found: {approval_id}")

    def require_approved(self, approval_id: str) -> Dict[str, Any]:
        record = next((item for item in self._read() if item["approval_id"] == approval_id), None)
        if not record or record["status"] != "APPROVED":
            raise PermissionError("A matching APPROVED approval is required before WMS write-back")
        return record

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        text = self.path.read_text().strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Keep the damaged file for inspection instead of discarding an audit trail.
            quarantine = self.path.with_suffix(f"{self.path.suffix}.corrupt-{int(time.time())}")
            os.replace(self.path, quarantine)
            LOG.error("Approval store was unreadable and has been moved to %s", quarantine)
            return []

    def _write(self, records: List[Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp-{uuid.uuid4().hex}")
        temporary.write_text(json.dumps(records, indent=2))
        os.replace(temporary, self.path)
