"""Approval persistence and OpenShell permission enforcement."""

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping

from services.config import ProductionConfig
from services.nvidia import PolicyDecision


class OpenShellPolicy:
    """Asks the OpenShell Governor for a grant, backed by a local allowlist."""

    def __init__(self, config: ProductionConfig):
        self.endpoint = config.openshell_url.rstrip("/")
        self.timeout = config.request_timeout_seconds
        self.allowed_tools = {"read_wms", "read_erp", "read_forecast", "load_policy", "solve_slotting", "create_approval", "write_wms"}

    def authorize(self, tool_name: str, actor: str, approval_id: str = "") -> PolicyDecision:
        local = self._local_allowlist(tool_name, approval_id, actor)
        # The local allowlist is a hard floor: the governor may only narrow it.
        if not local.allowed or not self.endpoint:
            return local
        return self._gate(tool_name, actor) or local

    def _local_allowlist(self, tool_name: str, approval_id: str, actor: str) -> PolicyDecision:
        if tool_name not in self.allowed_tools:
            return PolicyDecision(False, f"Tool is not allowlisted: {tool_name}", "openshell-tool-allowlist")
        if tool_name == "write_wms" and not approval_id:
            return PolicyDecision(False, "write_wms requires approval", "openshell-write-approval")
        return PolicyDecision(True, f"Authorized for actor {actor}", "openshell-local-policy")

    def _gate(self, tool_name: str, actor: str) -> PolicyDecision | None:
        """Returns None when the governor is unreachable so the local floor applies."""
        payload = json.dumps({"user": actor, "service": tool_name, "operation": "call"}).encode()
        request = urllib.request.Request(
            self.endpoint + "/api/v1/gate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                verdict = json.loads(response.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
            return None
        decision = str(verdict.get("decision", ""))
        reason = str(verdict.get("reason") or f"OpenShell decision: {decision}")
        return PolicyDecision(decision == "allowed", reason, "openshell-governor")


class ApprovalWorkflow:
    def __init__(self, store_path: str):
        self.path = Path(store_path)

    def create(self, moves: List[Mapping[str, Any]], requested_by: str, validation: Mapping[str, Any]) -> Dict[str, Any]:
        raw = json.dumps({"moves": moves, "requested_by": requested_by, "validation": validation}, sort_keys=True)
        approval = {"approval_id": "APR-" + hashlib.sha256(raw.encode()).hexdigest()[:12], "status": "PENDING", "requested_by": requested_by, "created_at": datetime.now(timezone.utc).isoformat(), "moves": moves, "validation": validation}
        records = self._read()
        records.append(approval)
        self._write(records)
        return approval

    def decide(self, approval_id: str, approver: str, approved: bool, reason: str = "") -> Dict[str, Any]:
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
        return json.loads(self.path.read_text())

    def _write(self, records: List[Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(records, indent=2))
        os.replace(temporary, self.path)
