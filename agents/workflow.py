"""LangGraph DeepAgent workflow for production warehouse optimization."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Mapping, TypedDict

from agents.digest import warehouse_digest
from agents.parsing import json_from_response
from agents.specialists import NemotronSpecialistRunner
from services.config import ProductionConfig
from services.enterprise import ERPAdapter, ForecastAdapter, WMSAdapter
from services.http_client import JsonHttpClient
from services.nvidia import CuOptClient, GuardrailsClient, NIMClient
from tools.governance import ApprovalWorkflow, OpenShellPolicy

# Warehouse operating policy is a small fixed rule set, so it is held in code rather than retrieved.
LOCAL_WAREHOUSE_POLICIES: List[Dict[str, Any]] = [
    {"policy_id": "cold-chain", "content": "Temperature-controlled SKUs must stay in cold-chain capable slots."},
    {"policy_id": "move-cap", "content": "Never exceed the planner move cap for the planning horizon."},
    {"policy_id": "locked-skus", "content": "SKUs listed as locked must not be relocated."},
    {"policy_id": "travel", "content": "Prioritize picker travel reduction for high-velocity SKUs."},
    {"policy_id": "windows", "content": "Schedule relocations only during low-volume shifts."},
]

# How long a run will sit waiting for an operator to answer an OpenShell request.
AUTHORIZATION_TIMEOUT_SECONDS = 600


class WarehouseState(TypedDict, total=False):
    business_goal: str
    actor: str
    constraints: Dict[str, Any]
    source_data: Dict[str, Any]
    data_digest: Dict[str, Any]
    policy_documents: List[Dict[str, Any]]
    plan: Dict[str, Any]
    specialist_analysis: Dict[str, Any]
    optimization_problem: Dict[str, Any]
    solution: Dict[str, Any]
    validation: Dict[str, Any]
    approval: Dict[str, Any]
    trace: List[Dict[str, Any]]
    guardrail_events: List[Dict[str, Any]]
    openshell_events: List[Dict[str, Any]]
    telemetry: Dict[str, Any]


class ProductionWarehouseWorkflow:
    """Production orchestration boundary with optional LangGraph runtime."""

    def __init__(self, config: ProductionConfig | None = None, specialist_runner: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None, wms: WMSAdapter | None = None, erp: ERPAdapter | None = None, forecast: ForecastAdapter | None = None, on_event: Callable[[str, Dict[str, Any]], None] | None = None):
        self.config = config or ProductionConfig.from_env()
        self.on_event = on_event
        self.wms = wms or WMSAdapter(JsonHttpClient(os.getenv("WMS_URL", "http://localhost:9001")), self.config.dry_run)
        self.erp = erp or ERPAdapter(JsonHttpClient(os.getenv("ERP_URL", "http://localhost:9002")))
        self.forecast = forecast or ForecastAdapter(JsonHttpClient(os.getenv("FORECAST_URL", "http://localhost:9003")))
        self.nim = NIMClient(self.config)
        self.subagent_nim = NIMClient.for_subagent(self.config)
        self.specialist_runner = specialist_runner or NemotronSpecialistRunner(self.subagent_nim, on_event=self._emit)
        self.guardrails = GuardrailsClient(self.config)
        self.openshell = OpenShellPolicy(self.config)
        self.approvals = ApprovalWorkflow(self.config.approval_store)
        self.cuopt = CuOptClient(self.config) if self.config.cuopt_url else None

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(kind, payload)

    def build_graph(self):
        """Build the LangGraph workflow; imports remain optional for offline development."""
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError("Install langgraph to build the production DeepAgent workflow") from exc

        graph = StateGraph(WarehouseState)
        graph.add_node("ingest", self._ingest)
        graph.add_node("load_policies", self._load_policies)
        graph.add_node("plan", self._plan)
        graph.add_node("specialists", self._specialists)
        graph.add_node("optimize", self._optimize)
        graph.add_node("validate", self._validate)
        graph.add_node("create_approval", self._create_approval)
        graph.add_edge(START, "ingest")
        graph.add_edge("ingest", "load_policies")
        graph.add_edge("load_policies", "plan")
        graph.add_edge("plan", "specialists")
        graph.add_edge("specialists", "optimize")
        graph.add_edge("optimize", "validate")
        graph.add_edge("validate", "create_approval")
        graph.add_edge("create_approval", END)
        return graph.compile()

    def run(self, business_goal: str, actor: str, constraints: Dict[str, Any] | None = None) -> WarehouseState:
        return self.build_graph().invoke({"business_goal": business_goal, "actor": actor, "constraints": constraints or {}, "trace": []})

    def write_back(self, approval_id: str, actor: str) -> Dict[str, Any]:
        decision = self.openshell.authorize("write_wms", actor, approval_id)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        approval = self.approvals.require_approved(approval_id)
        guardrail = self.guardrails.validate("tool_call", {"action": "wms_write", "approval_id": approval_id, "moves": approval["moves"]})
        if not guardrail.allowed:
            raise PermissionError(guardrail.reason)
        return self.wms.apply_approved_moves(approval["moves"], approval_id)

    def _ingest(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "ingest", "Reading WMS, ERP, and forecasting system snapshots")
        source_data = {"wms": self.wms.snapshot(), "erp": {"sku_master": self.erp.sku_master(), "inbound": self.erp.inbound_shipments()}, "forecast": self.forecast.forecast()}
        digest = warehouse_digest(source_data, state.get("constraints", {}))
        return {"source_data": source_data, "data_digest": digest}

    def _load_policies(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "load_policies", "Loading warehouse operating policies")
        return {"policy_documents": list(LOCAL_WAREHOUSE_POLICIES)}

    def _plan(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "plan", "Using Nemotron through NIM to produce a structured plan")
        self._authorize(state, "llm.supervisor", state["actor"])
        prompt = {"goal": state["business_goal"], "constraints": state.get("constraints", {}), "warehouse_summary": state.get("data_digest", {}), "policies": state.get("policy_documents", [])}
        self._guard(state, "input", prompt)
        started = time.perf_counter()
        response = self.nim.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the supervisor planner for warehouse slotting. You are given an aggregated "
                        "summary, not raw records; the solver holds the full data. Return only JSON shaped as "
                        '{"reasoning": ["step", ...], "objectives": ["..."], "weights": {"name": number}, '
                        '"binding_constraints": ["..."]}. Keep reasoning to at most five short steps.'
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, default=str)},
            ],
            json_only=True,
        )
        plan = json_from_response(response, "plan")
        usage = response.get("usage") or {}
        plan["telemetry"] = {
            "model": response.get("model", self.nim.model),
            "endpoint": self.nim.active_endpoint,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        }
        self._emit("plan_ready", {"plan": plan})
        return {"plan": plan}

    def _specialists(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "specialists", "Running demand, inventory, and warehouse specialist agents")
        self._authorize(state, "llm.subagent", state["actor"])
        return {"specialist_analysis": self.specialist_runner(state)}

    def _guard(self, state: WarehouseState, stage: str, payload: Mapping[str, Any]) -> None:
        """Guardrails are a gate, not an annotation: a block stops the run."""
        decision = self.guardrails.validate(stage, payload)
        record = {"stage": stage, "allowed": decision.allowed, "policy_id": decision.policy_id, "reason": decision.reason}
        state.setdefault("guardrail_events", []).append(record)
        self._emit("guardrail", record)
        if not decision.allowed:
            raise PermissionError(f"NeMo Guardrails blocked the {stage} stage: {decision.reason}")

    def _authorize(self, state: WarehouseState, tool: str, actor: str, approval_id: str = "") -> None:
        """Wait for the governor rather than failing: an approval is a pause, not an error."""
        deadline = time.time() + AUTHORIZATION_TIMEOUT_SECONDS
        announced = False
        while True:
            decision = self.openshell.authorize(tool, actor, approval_id)
            record = {"service": tool, "actor": actor, "allowed": decision.allowed, "policy_id": decision.policy_id, "reason": decision.reason}
            if decision.allowed:
                state.setdefault("openshell_events", []).append(record)
                self._emit("openshell", record)
                if announced:
                    self._emit("openshell_resumed", {"service": tool})
                return
            if not announced:
                announced = True
                state.setdefault("openshell_events", []).append(record)
                self._emit("openshell", record)
                self._emit("openshell_halt", {"service": tool, "reason": decision.reason})
            if time.time() > deadline:
                raise PermissionError(f"OpenShell did not grant {tool} within {AUTHORIZATION_TIMEOUT_SECONDS} seconds: {decision.reason}")
            time.sleep(3)

    def _optimize(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "optimize", "Submitting a formal constrained slotting problem to cuOpt")
        problem = {
            "goal": state["business_goal"],
            "planning_horizon_days": 7,
            "constraints": state.get("constraints", {}),
            "plan": state.get("plan", {}),
            "analysis": state.get("specialist_analysis", {}),
            "source_data": state.get("source_data", {}),
            "required_output": {"moves": ["sku_id", "from_slot", "to_slot", "day", "window", "reason", "benefit_hours_per_day", "labor_minutes", "confidence"], "metrics": ["travel_reduction_pct", "replenishment_reduction_pct", "constraint_violations", "plan_value"]},
        }
        if not self.cuopt:
            raise RuntimeError("CUOPT_URL is not configured; the plan can only come from the cuOpt solver")
        self._authorize(state, "solve_slotting", state["actor"])
        started = time.perf_counter()
        solution = self.cuopt.solve_slotting(problem)
        solution["telemetry"] = {
            "endpoint": self.config.cuopt_url,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "candidate_skus": len(problem["source_data"].get("erp", {}).get("sku_master", {}).get("sku_master", [])),
            "slots": len(problem["source_data"].get("wms", {}).get("warehouse_layout", [])),
        }
        self._emit("cuopt_solved", {"solution": solution})
        return {"optimization_problem": problem, "solution": solution}

    def _validate(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "validate", "Applying guardrails and OpenShell policy before approval")
        solution = state.get("solution", {})
        self._authorize(state, "create_approval", state["actor"])
        self._guard(state, "output", solution)
        validation = {"openshell_allowed": True, "guardrails_allowed": True, "policy_id": "nemo-guardrails", "status": "PASSED"}
        return {"validation": validation}

    def _create_approval(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "create_approval", "Creating pending human approval; no WMS write occurs")
        moves = list(state.get("solution", {}).get("moves", state.get("solution", {}).get("recommendations", [])))
        approval = self.approvals.create(moves, state["actor"], state["validation"])
        return {"approval": approval}

    def _trace(self, state: WarehouseState, node: str, message: str) -> None:
        state.setdefault("trace", []).append({"node": node, "message": message})
        self._emit("stage", {"node": node, "message": message})

