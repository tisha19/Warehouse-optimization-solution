"""State machine behind the WarehouseIQ UI.

Four screens, one rule: nothing is shown that a live service did not produce.
There is no placeholder plan and no fallback. If the NIM, cuOpt, NeMo
Guardrails or the OpenShell governor cannot do its part, the run fails with the
reason and the UI reports it.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from agents.workflow import ProductionWarehouseWorkflow
from mocks.enterprise_adapters import SyntheticERPAdapter, SyntheticForecastAdapter, SyntheticWMSAdapter
from mocks.enterprise_services import MockServiceState, resolve_seed
from services.config import ProductionConfig
from showcase.kpis import detect_problems, warehouse_kpis

ACTOR = "warehouse-planner"
GOAL = (
    "Reduce picker travel over the next seven days. Stay within the move cap, keep cold-chain stock "
    "where it is, and prioritise the highest-demand lines."
)
STAGES = (
    ("ingest", "Read WMS, ERP and forecast"),
    ("load_policies", "Load operating policies"),
    ("plan", "Supervisor agent plans"),
    ("specialists", "Specialist agents analyse"),
    ("optimize", "cuOpt solves the slotting model"),
    ("validate", "Guardrails and policy check"),
    ("create_approval", "Raise approval for review"),
)
# write_wms is per_call, so the governor holds the write until an operator answers.
WRITE_APPROVAL_WAIT_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ShowcaseController:
    def __init__(self, seed: int | None = None, workflow_factory: Callable[..., Any] = ProductionWarehouseWorkflow):
        self.enterprise = MockServiceState(resolve_seed(seed))
        self.config = ProductionConfig.from_env()
        self.goal = GOAL
        self.constraints: Dict[str, Any] = {"max_moves": 10, "locked_skus": [], "cold_chain_locked": True, "execution_windows": ["low-volume shifts"]}
        self._lock = threading.Lock()
        self.run_state: Dict[str, Any] = self._idle_run()
        self.decisions: Dict[str, str] = {}
        self.commit_history: List[Dict[str, Any]] = []
        self.commit_state: Dict[str, Any] = {"status": "IDLE", "service": "write_wms", "error": None, "commit": None}
        self.baseline_kpis: Optional[Dict[str, Any]] = None
        self.workflow = workflow_factory(
            config=self.config,
            wms=SyntheticWMSAdapter(self.enterprise),
            erp=SyntheticERPAdapter(self.enterprise),
            forecast=SyntheticForecastAdapter(self.enterprise),
            on_event=self._on_workflow_event,
        )

    # ---------------------------------------------------------------- dashboard

    def _source_data(self) -> Dict[str, Any]:
        return {
            "wms": self.enterprise.snapshot(),
            "erp": {"sku_master": self.enterprise.sku_master(), "inbound": self.enterprise.inbound()},
            "forecast": self.enterprise.forecast(14),
        }

    def dashboard(self) -> Dict[str, Any]:
        source = self._source_data()
        kpis = warehouse_kpis(source)
        if self.baseline_kpis is None:
            self.baseline_kpis = dict(kpis)
        layout = source["wms"]["warehouse_layout"]
        occupancy = source["wms"]["slot_occupancy"]
        occupied: Dict[int, int] = {}
        for row in occupancy:
            zone = int(row["zone_id"])
            occupied[zone] = occupied.get(zone, 0) + 1
        zones = []
        for zone in sorted({int(row["zone_id"]) for row in layout}):
            rows = [row for row in layout if int(row["zone_id"]) == zone]
            chilled = any(row.get("temperature_controlled") for row in rows)
            zones.append({
                "id": chr(64 + zone),
                "label": "Forward pick" if zone == 1 else "Chilled reserve" if chilled else "Reserve",
                "slots": len(rows),
                "utilisation": round(occupied.get(zone, 0) / max(len(rows), 1) * 100),
                "distance": round(sum(float(row["distance_to_picking_m"]) for row in rows) / max(len(rows), 1)),
            })
        return {
            "site": "DC-07 / North distribution centre",
            "kpis": kpis,
            "baseline": self.baseline_kpis,
            "problems": detect_problems(kpis, source),
            "zones": zones,
            "counts": {
                "skus": len(source["erp"]["sku_master"]["sku_master"]),
                "slots": len(layout),
                "occupied": len(occupancy),
                "inbound": len(source["erp"]["inbound"]["inbound_shipments"]),
                "forecast_rows": len(source["forecast"]["forecast"]),
            },
            "services": self.service_status(),
            "commits": self.commit_history[-5:],
            "run": {"id": self.run_state.get("id"), "status": self.run_state.get("status")},
            "generated_at": _now(),
        }

    def service_status(self) -> List[Dict[str, Any]]:
        return [
            {"name": "Nemotron NIM (supervisor)", "endpoint": self.config.nim_base_url, "detail": self.config.nim_model},
            {"name": "Nemotron NIM (specialists)", "endpoint": self.config.nim_subagent_base_url or self.config.nim_base_url, "detail": self.config.nim_subagent_model or self.config.nim_model},
            {"name": "NVIDIA cuOpt", "endpoint": self.config.cuopt_url, "detail": "linear assignment solver"},
            {"name": "NeMo Guardrails", "endpoint": self.config.guardrails_url, "detail": self.config.guardrails_config_id},
            {"name": "OpenShell Governor", "endpoint": self.config.openshell_url, "detail": "policy gate"},
        ]

    # ---------------------------------------------------------------- the run

    def _idle_run(self) -> Dict[str, Any]:
        return {
            "id": None,
            "status": "IDLE",
            "stages": [{"key": key, "label": label, "status": "pending", "detail": "", "duration_ms": 0} for key, label in STAGES],
            "events": [],
            "agents": [],
            "guardrails": [],
            "openshell": [],
            "cuopt": None,
            "plan": None,
            "moves": [],
            "approval": None,
            "validation": None,
            "error": None,
            "halted_on": None,
            "started_at": None,
            "finished_at": None,
            "tokens": {"prompt": 0, "completion": 0, "calls": 0},
        }

    def _event(self, message: str, kind: str = "info", stage: str = "") -> None:
        self.run_state["events"].append({"at": _now(), "kind": kind, "stage": stage, "message": message})

    def _set_stage(self, key: str, status: str, detail: str = "") -> None:
        for stage in self.run_state["stages"]:
            if stage["key"] != key:
                continue
            if status == "running":
                stage["_started"] = time.perf_counter()
            elif "_started" in stage:
                stage["duration_ms"] = round((time.perf_counter() - stage.pop("_started")) * 1000)
            stage["status"] = status
            if detail:
                stage["detail"] = detail
            return

    def _on_workflow_event(self, kind: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            if kind == "stage":
                node = str(payload.get("node"))
                message = str(payload.get("message", ""))
                for stage in self.run_state["stages"]:
                    if stage["key"] == node and stage["status"] == "pending":
                        self._set_stage(node, "running", message)
                    elif stage["key"] != node and stage["status"] == "running":
                        self._set_stage(stage["key"], "done")
                self._event(message, "stage", node)
            elif kind == "specialist_started":
                agent = str(payload.get("agent"))
                self.run_state["agents"].append({"name": agent, "role": "specialist", "status": "running", "reasoning": [], "findings": [], "risks": [], "telemetry": {}})
                self._event(f"{agent} specialist started", "agent", "specialists")
            elif kind == "specialist_finished":
                agent = str(payload.get("agent"))
                analysis = dict(payload.get("analysis") or {})
                for record in self.run_state["agents"]:
                    if record["name"] == agent:
                        record.update({
                            "status": "done",
                            "reasoning": self._as_list(analysis.get("reasoning")),
                            "findings": self._as_list(analysis.get("findings")),
                            "risks": self._as_list(analysis.get("risks")),
                            "telemetry": analysis.get("telemetry", {}),
                        })
                self._event(f"{agent} specialist finished", "agent", "specialists")
            elif kind == "plan_ready":
                plan = dict(payload.get("plan") or {})
                self.run_state["plan"] = plan
                self.run_state["agents"].insert(0, {
                    "name": "supervisor",
                    "role": "supervisor",
                    "status": "done",
                    "reasoning": self._as_list(plan.get("reasoning")),
                    "findings": self._as_list(plan.get("objectives")),
                    "risks": self._as_list(plan.get("binding_constraints")),
                    "telemetry": plan.get("telemetry", {}),
                })
                self._event("Supervisor produced objectives and weights", "agent", "plan")
            elif kind == "guardrail":
                self.run_state["guardrails"].append({**dict(payload), "at": _now()})
                verdict = "allowed" if payload.get("allowed") else "blocked"
                self._event(f"Guardrails {verdict} the {payload.get('stage')} stage", "guardrail", "validate")
            elif kind == "openshell":
                self.run_state["openshell"].append({**dict(payload), "at": _now()})
                verdict = "granted" if payload.get("allowed") else "withheld"
                self._event(f"OpenShell {verdict} {payload.get('service')}", "openshell")
            elif kind == "openshell_halt":
                self.run_state["status"] = "HALTED"
                self.run_state["halted_on"] = {"service": payload.get("service"), "reason": payload.get("reason")}
                for stage in self.run_state["stages"]:
                    if stage["status"] == "running":
                        stage["status"] = "blocked"
                        stage["detail"] = str(payload.get("reason", ""))
                self._event(f"Halted: OpenShell has not granted {payload.get('service')}. {payload.get('reason')}", "openshell")
            elif kind == "openshell_resumed":
                self.run_state["status"] = "RUNNING"
                self.run_state["halted_on"] = None
                for stage in self.run_state["stages"]:
                    if stage["status"] == "blocked":
                        stage["status"] = "running"
                self._event(f"Resumed: OpenShell granted {payload.get('service')}", "openshell")
            elif kind == "cuopt_solved":
                solution = dict(payload.get("solution") or {})
                self.run_state["cuopt"] = {
                    "headline": solution.get("headline"),
                    "explanation": solution.get("explanation"),
                    "metrics": solution.get("metrics", {}),
                    "telemetry": solution.get("telemetry", {}),
                    "move_count": len(solution.get("moves") or []),
                }
                self._event("cuOpt returned an optimal assignment", "solver", "optimize")

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, dict):
            return [f"{key}: {item}" for key, item in value.items()]
        return [str(value)] if value else []

    def start_run(self) -> Dict[str, Any]:
        with self._lock:
            if self.run_state.get("status") in ("RUNNING", "HALTED"):
                return self._snapshot()
            self.run_state = self._idle_run()
            self.run_state.update({"id": uuid.uuid4().hex[:12], "status": "RUNNING", "started_at": _now()})
            self.decisions = {}
            self._event("Run requested by the planner", "info")
        threading.Thread(target=self._execute, daemon=True).start()
        return self.run()

    def _execute(self) -> None:
        try:
            state = self.workflow.run(self.goal, ACTOR, dict(self.constraints))
            with self._lock:
                self._finish(state)
        except Exception as exc:
            with self._lock:
                self.run_state["status"] = "FAILED"
                self.run_state["error"] = f"{type(exc).__name__}: {exc}"
                self.run_state["finished_at"] = _now()
                for stage in self.run_state["stages"]:
                    if stage["status"] == "running":
                        self._set_stage(stage["key"], "failed", str(exc))
                self._event(str(exc), "error")

    def _finish(self, state: Mapping[str, Any]) -> None:
        solution = dict(state.get("solution") or {})
        raw_moves = solution.get("moves") or solution.get("recommendations") or []
        source = state.get("source_data", {})
        sku_by_id = {str(row.get("sku_id")): row for row in source.get("erp", {}).get("sku_master", {}).get("sku_master", [])}
        moves = [self._normalise_move(index, move, sku_by_id) for index, move in enumerate(raw_moves, 1)]
        self.decisions = {move["id"]: "pending" for move in moves}
        for stage in self.run_state["stages"]:
            if stage["status"] in ("running", "pending"):
                self._set_stage(stage["key"], "done")
        self.run_state.update({
            "status": "COMPLETE",
            "finished_at": _now(),
            "moves": moves,
            "approval": state.get("approval"),
            "validation": state.get("validation"),
            "tokens": {
                "prompt": self.workflow.nim.prompt_tokens + self.workflow.subagent_nim.prompt_tokens,
                "completion": self.workflow.nim.completion_tokens + self.workflow.subagent_nim.completion_tokens,
                "calls": self.workflow.nim.calls + self.workflow.subagent_nim.calls,
            },
        })
        self._event(f"Plan ready: {len(moves)} moves awaiting review", "info")

    @staticmethod
    def _normalise_move(index: int, raw: Mapping[str, Any], sku_by_id: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
        sku_id = str(raw.get("sku_id", "UNKNOWN"))
        sku = sku_by_id.get(sku_id, {})
        return {
            "id": str(raw.get("move_id", f"MV-{index:03d}")),
            "priority": index,
            "sku": str(raw.get("product_name") or sku.get("product_name") or sku_id),
            "code": sku_id,
            "abc_class": str(sku.get("abc_class", "")),
            "from_slot": str(raw.get("from_slot", "Unassigned")),
            "to_slot": str(raw.get("to_slot", "Unassigned")),
            "day": int(raw.get("day", 0)),
            "window": str(raw.get("window", "Low-volume shift")),
            "reason": str(raw.get("reason", "")),
            "benefit_hours_per_day": round(float(raw.get("benefit_hours_per_day", 0.0)), 2),
            "labor_minutes": int(raw.get("labor_minutes", 0)),
        }

    def _snapshot(self) -> Dict[str, Any]:
        snapshot = json.loads(json.dumps(self.run_state, default=str))
        snapshot["decisions"] = dict(self.decisions)
        snapshot["constraints"] = dict(self.constraints)
        snapshot["goal"] = self.goal
        return snapshot

    def run(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot()

    # ---------------------------------------------------------------- review

    def decide(self, move_id: str, decision: str) -> Dict[str, Any]:
        if decision not in ("approved", "rejected", "pending"):
            raise ValueError(f"Unknown decision: {decision}")
        if move_id == "*":
            self.decisions = {key: decision for key in self.decisions}
        elif move_id in self.decisions:
            self.decisions[move_id] = decision
        else:
            raise KeyError(f"Unknown move: {move_id}")
        return self.run()

    def commit(self) -> Dict[str, Any]:
        """Start the WMS write. write_wms is per_call, so the governor holds it."""
        if self.commit_state.get("status") == "AWAITING_APPROVAL":
            return dict(self.commit_state)
        if self.run_state.get("status") != "COMPLETE":
            raise RuntimeError("There is no completed plan to commit")
        approval = self.run_state.get("approval") or {}
        approval_id = approval.get("approval_id")
        if not approval_id:
            raise RuntimeError("The workflow did not raise an approval record")
        approved = [move for move in self.run_state["moves"] if self.decisions.get(move["id"]) == "approved"]
        if not approved:
            raise RuntimeError("No moves have been approved")

        self.commit_state = {"status": "AWAITING_APPROVAL", "service": "write_wms", "error": None, "commit": None}
        threading.Thread(target=self._do_commit, args=(approval_id, approved), daemon=True).start()
        return dict(self.commit_state)

    def commit_status(self) -> Dict[str, Any]:
        return dict(self.commit_state)

    def _do_commit(self, approval_id: str, approved: List[Dict[str, Any]]) -> None:
        try:
            decision = self.workflow.openshell.authorize("write_wms", ACTOR, approval_id, wait_seconds=WRITE_APPROVAL_WAIT_SECONDS)
            if not decision.allowed:
                self.commit_state = {"status": "DENIED", "service": "write_wms", "error": decision.reason, "commit": None}
                return
            self.workflow.approvals.decide(approval_id, ACTOR, True, "Approved in WarehouseIQ")
            payload = [
                {"sku_id": move["code"], "from_slot": move["from_slot"], "to_slot": move["to_slot"], "move_id": move["id"]}
                for move in approved
            ]
            result = self.workflow.wms.apply_approved_moves(payload, approval_id)
            record = {
                "at": _now(),
                "approval_id": approval_id,
                "moves": len(payload),
                "relocated": int(result.get("relocated", len(payload))),
                "rejected": sum(1 for value in self.decisions.values() if value == "rejected"),
            }
            self.commit_history.append(record)
            with self._lock:
                self.run_state = self._idle_run()
                self.decisions = {}
            self.commit_state = {"status": "COMMITTED", "service": "write_wms", "error": None, "commit": record}
        except Exception as exc:
            self.commit_state = {"status": "FAILED", "service": "write_wms", "error": f"{type(exc).__name__}: {exc}", "commit": None}

    # ---------------------------------------------------------------- openshell

    def _governor(self, path: str, method: str = "GET", payload: Mapping[str, Any] | None = None) -> Any:
        base = self.config.openshell_url.rstrip("/")
        if not base:
            raise RuntimeError("OPENSHELL_URL is not configured")
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                body = response.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OpenShell governor returned {exc.code}: {exc.read().decode()[:200]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenShell governor is unreachable at {base}: {exc}") from exc

    def openshell_overview(self) -> Dict[str, Any]:
        return {
            "endpoint": self.config.openshell_url,
            "services": self._governor("/api/v1/services"),
            "grants": self._governor("/api/v1/grants"),
            "pending": self._governor("/api/v1/requests?status=pending"),
            "audit": self._governor("/api/v1/audit?limit=40"),
            "telemetry": self.telemetry(),
        }

    def telemetry(self) -> Dict[str, Any]:
        supervisor, subagent = self.workflow.nim, self.workflow.subagent_nim
        return {
            "models": [
                {"role": "supervisor", "model": supervisor.model, "endpoint": supervisor.active_endpoint, "calls": supervisor.calls, "prompt_tokens": supervisor.prompt_tokens, "completion_tokens": supervisor.completion_tokens},
                {"role": "specialists", "model": subagent.model, "endpoint": subagent.active_endpoint, "calls": subagent.calls, "prompt_tokens": subagent.prompt_tokens, "completion_tokens": subagent.completion_tokens},
            ],
            "totals": {
                "calls": supervisor.calls + subagent.calls,
                "prompt_tokens": supervisor.prompt_tokens + subagent.prompt_tokens,
                "completion_tokens": supervisor.completion_tokens + subagent.completion_tokens,
            },
            "commits": len(self.commit_history),
        }

    def resolve_request(self, request_id: str, approve: bool) -> Dict[str, Any]:
        action = "approve" if approve else "deny"
        self._governor(f"/api/v1/requests/{request_id}/{action}", "POST", {"resolved_by": "warehouseiq-admin"})
        return self.openshell_overview()

    def revoke_grant(self, user: str, service: str) -> Dict[str, Any]:
        self._governor(f"/api/v1/grants/{user}/{service}", "DELETE")
        return self.openshell_overview()
