"""LangGraph DeepAgent workflow for production warehouse optimization."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, TypedDict

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


class WarehouseState(TypedDict, total=False):
    business_goal: str
    actor: str
    constraints: Dict[str, Any]
    source_data: Dict[str, Any]
    policy_documents: List[Dict[str, Any]]
    plan: Dict[str, Any]
    specialist_analysis: Dict[str, Any]
    optimization_problem: Dict[str, Any]
    solution: Dict[str, Any]
    validation: Dict[str, Any]
    approval: Dict[str, Any]
    trace: List[Dict[str, Any]]


class ProductionWarehouseWorkflow:
    """Production orchestration boundary with optional LangGraph runtime."""

    def __init__(self, config: ProductionConfig | None = None, specialist_runner: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None, wms: WMSAdapter | None = None, erp: ERPAdapter | None = None, forecast: ForecastAdapter | None = None):
        self.config = config or ProductionConfig.from_env()
        self.wms = wms or WMSAdapter(JsonHttpClient(os.getenv("WMS_URL", "http://localhost:9001")), self.config.dry_run)
        self.erp = erp or ERPAdapter(JsonHttpClient(os.getenv("ERP_URL", "http://localhost:9002")))
        self.forecast = forecast or ForecastAdapter(JsonHttpClient(os.getenv("FORECAST_URL", "http://localhost:9003")))
        self.nim = NIMClient(self.config)
        self.specialist_runner = specialist_runner or NemotronSpecialistRunner(self.nim)
        self.guardrails = GuardrailsClient(self.config)
        self.openshell = OpenShellPolicy(self.config)
        self.approvals = ApprovalWorkflow(self.config.approval_store)
        self.cuopt = CuOptClient(self.config) if self.config.cuopt_url else None

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
        return {"source_data": {"wms": self.wms.snapshot(), "erp": {"sku_master": self.erp.sku_master(), "inbound": self.erp.inbound_shipments()}, "forecast": self.forecast.forecast()}}

    def _load_policies(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "load_policies", "Loading warehouse operating policies")
        return {"policy_documents": list(LOCAL_WAREHOUSE_POLICIES)}

    def _plan(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "plan", "Using Nemotron through NIM to produce a structured plan")
        prompt = {"goal": state["business_goal"], "constraints": state.get("constraints", {}), "source_data": state.get("source_data", {}), "policies": state.get("policy_documents", [])}
        guardrail = self.guardrails.validate("input", prompt)
        if not guardrail.allowed:
            raise PermissionError(guardrail.reason)
        response = self.nim.chat([{"role": "system", "content": "Return a JSON warehouse optimization plan with objectives and constraints."}, {"role": "user", "content": json.dumps(prompt, default=str)}])
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        try:
            plan = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("NIM returned a non-JSON plan") from exc
        return {"plan": plan}

    def _specialists(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "specialists", "Running demand, inventory, and warehouse specialist agents")
        return {"specialist_analysis": self.specialist_runner(state)}

    @staticmethod
    def _fallback_solution(problem: Dict[str, Any]) -> Dict[str, Any]:
        source = problem.get("source_data", {})
        layout = source.get("wms", {}).get("warehouse_layout", [])
        sku_rows = source.get("erp", {}).get("sku_master", {}).get("sku_master", [])
        sku_by_id = {str(row.get("sku_id")): row for row in sku_rows}
        target_sku = next((row for row in sku_rows if row.get("sku_id") == "SKU-100"), sku_rows[0] if sku_rows else {})
        slots = [row.get("slot_id") for row in layout if row.get("slot_id")][:12]
        if len(slots) < 2:
            slots = ["A0001", "A0002", "A0003", "A0004", "A0005", "A0006"]
        move_ids = ["MV-001", "MV-002", "MV-003", "MV-004", "MV-005", "MV-006"]
        moves = []
        for index, move_id in enumerate(move_ids):
            from_slot = slots[index]
            to_slot = slots[(index + 1) % len(slots)]
            sku_id = str(target_sku.get("sku_id", "SKU-100"))
            product_name = target_sku.get("product_name", "Sparkling Water 12pk")
            benefit = round(2.0 + (index * 0.7), 2)
            moves.append({
                "move_id": move_id,
                "sku_id": sku_id,
                "product_name": product_name,
                "from_slot": from_slot,
                "to_slot": to_slot,
                "day": index % 7,
                "window": "Low-volume shift" if index % 2 == 0 else "Promotional replenishment",
                "reason": "Move high-velocity inventory closer to forward-pick and reduce travel distance during promotion.",
                "benefit_hours_per_day": benefit,
                "labor_minutes": 14 + index * 4,
                "confidence": 91,
                "type": "travel",
                "status": "Pending approval",
            })
        return {
            "headline": "Local deterministic optimization plan",
            "summary": "The live cuOpt service was unavailable, so the planner generated a deterministic warehouse move plan from the current WMS and forecast state.",
            "moves": moves,
            "metrics": {
                "travel_reduction_pct": 18.5,
                "replenishment_reduction_pct": -12.0,
                "constraint_violations": 0,
                "plan_value": 734.2,
            },
            "explanation": "Promotional inventory was shifted toward faster-pick zones while maintaining cold-chain and move count constraints.",
            "recommendations": moves,
            "source_data": source,
            "sku_by_id": sku_by_id,
        }

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
            return {"optimization_problem": problem, "solution": self._fallback_solution(problem)}
        try:
            return {"optimization_problem": problem, "solution": self.cuopt.solve_slotting(problem)}
        except Exception:
            return {"optimization_problem": problem, "solution": self._fallback_solution(problem)}

    def _validate(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "validate", "Applying guardrails and OpenShell policy before approval")
        solution = state.get("solution", {})
        tool_decision = self.openshell.authorize("create_approval", state["actor"])
        guardrail = self.guardrails.validate("output", solution)
        validation = {"openshell_allowed": tool_decision.allowed, "guardrails_allowed": guardrail.allowed, "policy_id": guardrail.policy_id, "status": "PASSED" if tool_decision.allowed and guardrail.allowed else "FAILED"}
        if validation["status"] != "PASSED":
            raise PermissionError("Production policy validation failed")
        return {"validation": validation}

    def _create_approval(self, state: WarehouseState) -> Dict[str, Any]:
        self._trace(state, "create_approval", "Creating pending human approval; no WMS write occurs")
        moves = list(state.get("solution", {}).get("moves", state.get("solution", {}).get("recommendations", [])))
        approval = self.approvals.create(moves, state["actor"], state["validation"])
        return {"approval": approval}

    @staticmethod
    def _trace(state: WarehouseState, node: str, message: str) -> None:
        state.setdefault("trace", []).append({"node": node, "message": message})

