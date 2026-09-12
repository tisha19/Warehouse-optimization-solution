"""The warehouse orchestrator: a deepagents agent on hosted Nemotron 3 Ultra.

This replaces the fixed LangGraph pipeline. That pipeline always ran the same
seven nodes once each, which meant one call to cuOpt and no way to react to the
result. Slotting is not a one-shot problem: the first solve reveals what is
actually binding, and a second pass with a different move cap or objective often
clears what the first one left behind.

So the sequence is the model's to choose. It reads the measured state, delegates
to the specialists, solves, looks at what the solve would actually buy, and
solves again while addressable headroom remains. The human-set constraints in the
left rail stay fixed; what the orchestrator chooses is how hard to push inside
them -- the move count and the solver's time budget.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

from agents.digest import warehouse_digest
from services.config import ProductionConfig
from services.enterprise import ERPAdapter, ForecastAdapter, WMSAdapter
from services.harness_profile import profile_report, specialist_model, supervisor_model
from services.http_client import JsonHttpClient, ServiceError
from services.nvidia import CuOptClient, GuardrailsClient
from showcase.kpis import project_moves, slotting_headroom, warehouse_kpis
from tools.governance import ApprovalWorkflow, OpenShellPolicy

# cuOpt is fast enough that a long budget only delays the next pass; the
# orchestrator picks inside this ceiling.
MAX_SOLVER_SECONDS = 60
# Enough passes to converge, few enough that a stuck loop still terminates.
MAX_SOLVE_ROUNDS = 6
# An unhealthy solver fails every call, so retrying it just hangs the run.
MAX_SOLVER_FAILURES = 2
AUTHORIZATION_TIMEOUT_SECONDS = 600

SPECIALIST_BRIEFS = {
    "demand_specialist": (
        "You assess demand for a warehouse slotting decision: which lines are accelerating, "
        "which are promotional, and where the picking volume concentrates."
    ),
    "inventory_specialist": (
        "You assess inventory for a warehouse slotting decision: cover, replenishment pressure, "
        "and lines at risk of running dry in the forward pick face."
    ),
    "warehouse_specialist": (
        "You assess the physical operation for a slotting decision: travel, congestion, "
        "slot capacity and handling constraints."
    ),
}

ORCHESTRATOR_PROMPT = """You are the orchestrator for warehouse slotting optimisation at {site}.

Your goal: {goal}

The operator has set these constraints. They are fixed and you must never exceed them:
{constraints}

How to work:
1. Call `read_warehouse_state` first. It returns measured facts, not estimates.
2. Delegate to `demand_specialist`, `inventory_specialist` and `warehouse_specialist`
   for judgement about what those facts mean. Give each a specific question.
3. Call `solve_slotting` to run the cuOpt solver. You choose `max_moves` (never above
   the operator's cap of {max_moves}) and `time_limit_s` (at most {max_solver_seconds}).
   Start at {max_solver_seconds}: a constrained layout often needs the whole budget, and
   a solve that finds nothing costs more time than one that succeeds.
4. One solve is rarely the best solve. The result tells you what the moves bought and
   how much addressable headroom is left. The solver is deterministic for a given move
   count, so exploring means varying `max_moves`, not rewording the objective. While
   headroom remains and you have rounds left, try a different move count and compare.
   The best round is kept automatically. Stop when a further pass stops improving the
   outcome, not after the first result.
5. When no addressable headroom remains, or further passes stop helping, call
   `create_approval` naming the round you recommend. Do this once, as your last action,
   after you have finished exploring. More moves is not automatically better: if a
   smaller round captures most of the benefit for much less labour, approve that one and
   say why. If the operator's move cap is what limits the remaining benefit, say so
   explicitly. Never claim the moves are applied: an operator approves them separately.

Judgement rules:
- Every number you state must come from a tool result. Never invent a figure.
- More moves is not better. Each move costs labour; propose the smallest set that
  captures the available benefit.
- If the state shows no addressable headroom at the start, say so and do not solve.
"""


class UsageRecorder(BaseCallbackHandler):
    """Reports every model answer, including ones nested inside a tool call."""

    def __init__(self, record: Callable[[str, int, int], None]) -> None:
        self._record = record

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:  # noqa: ANN401
        for generations in getattr(response, "generations", None) or []:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not usage:
                    continue
                meta = getattr(message, "response_metadata", None) or {}
                self._record(
                    str(meta.get("model_name") or meta.get("model") or "unknown"),
                    int(usage.get("input_tokens", 0) or 0),
                    int(usage.get("output_tokens", 0) or 0),
                )


class WarehouseDeepAgent:
    """Builds and runs the orchestrator. One instance per process; runs are serialised."""

    def __init__(
        self,
        config: ProductionConfig | None = None,
        wms: WMSAdapter | None = None,
        erp: ERPAdapter | None = None,
        forecast: ForecastAdapter | None = None,
        on_event: Callable[[str, Dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config or ProductionConfig.from_env()
        self.on_event = on_event
        self.wms = wms or WMSAdapter(JsonHttpClient(os.getenv("WMS_URL", "http://localhost:9001")), self.config.dry_run)
        self.erp = erp or ERPAdapter(JsonHttpClient(os.getenv("ERP_URL", "http://localhost:9002")))
        self.forecast = forecast or ForecastAdapter(JsonHttpClient(os.getenv("FORECAST_URL", "http://localhost:9003")))
        self.guardrails = GuardrailsClient(self.config)
        self.openshell = OpenShellPolicy(self.config)
        self.approvals = ApprovalWorkflow(self.config.approval_store)
        self.cuopt = CuOptClient(self.config) if self.config.cuopt_url else None
        self._lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._run: Dict[str, Any] = {}

    # ------------------------------------------------------------------ events

    def _emit(self, kind: str, payload: Dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(kind, payload)

    def _record_usage(self, model: str, prompt: int, completion: int) -> None:
        """One place for token accounting; subagent turns arrive on another thread."""
        with self._usage_lock:
            totals = self._run.setdefault(
                "usage", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "by_model": {}}
            )
            totals["calls"] += 1
            totals["prompt_tokens"] += prompt
            totals["completion_tokens"] += completion
            bucket = totals.setdefault("by_model", {}).setdefault(
                model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += prompt
            bucket["completion_tokens"] += completion
            snapshot = json.loads(json.dumps(totals))
        # Egress is worth watching while it happens, not only once it is over.
        self._emit("usage", snapshot)

    def _guard(self, stage: str, payload: Mapping[str, Any]) -> None:
        """Guardrails are a gate, not an annotation: a block stops the run."""
        decision = self.guardrails.validate(stage, payload)
        record = {
            "stage": stage,
            "allowed": decision.allowed,
            "policy_id": decision.policy_id,
            "reason": decision.reason,
        }
        self._run.setdefault("guardrail_events", []).append(record)
        self._emit("guardrail", record)
        if not decision.allowed:
            raise PermissionError(f"NeMo Guardrails blocked the {stage} stage: {decision.reason}")

    def _authorize(self, service: str, actor: str, approval_id: str = "") -> None:
        """Wait for the governor rather than failing: an approval is a pause, not an error."""
        deadline = time.time() + AUTHORIZATION_TIMEOUT_SECONDS
        announced = False
        while True:
            decision = self.openshell.authorize(service, actor, approval_id)
            record = {
                "service": service,
                "actor": actor,
                "allowed": decision.allowed,
                "policy_id": decision.policy_id,
                "reason": decision.reason,
            }
            if decision.allowed:
                self._run.setdefault("openshell_events", []).append(record)
                self._emit("openshell", record)
                if announced:
                    self._emit("openshell_resumed", {"service": service})
                return
            if not announced:
                announced = True
                self._run.setdefault("openshell_events", []).append(record)
                self._emit("openshell", record)
                self._emit("openshell_halt", {"service": service, "reason": decision.reason})
            if time.time() > deadline:
                raise PermissionError(
                    f"OpenShell did not grant {service} within {AUTHORIZATION_TIMEOUT_SECONDS} seconds: {decision.reason}"
                )
            time.sleep(3)

    # ------------------------------------------------------------------- state

    def _source_data(self) -> Dict[str, Any]:
        return {
            "wms": self.wms.snapshot(),
            "erp": {"sku_master": self.erp.sku_master(), "inbound": self.erp.inbound_shipments()},
            "forecast": self.forecast.forecast(),
        }

    def _measure(self, source: Mapping[str, Any], constraints: Mapping[str, Any]) -> Dict[str, Any]:
        return {"kpis": warehouse_kpis(source), "headroom": slotting_headroom(source, constraints)}

    # ------------------------------------------------------------------- tools

    def _build_tools(self, actor: str, constraints: Dict[str, Any]) -> List[Any]:
        agent = self

        @tool
        def read_warehouse_state() -> str:
            """Read the live WMS, ERP and forecast snapshot and return the measured KPIs, the
            slotting headroom a further run could still remove, and an aggregated summary of
            the warehouse. Call this before solving."""
            # Auto-approved by policy, but the audit should still show that the
            # agent reached into all three enterprise systems.
            for service in ("read_wms", "read_erp", "read_forecast"):
                agent._authorize(service, actor)
            source = agent._source_data()
            agent._run["source"] = source
            measured = agent._measure(source, constraints)
            digest = warehouse_digest(source, constraints)
            agent._run["measured"] = measured
            agent._emit("state_read", {"kpis": measured["kpis"], "headroom": measured["headroom"]})
            return json.dumps(
                {
                    "kpis": measured["kpis"],
                    "slotting_headroom": measured["headroom"],
                    "warehouse_summary": digest,
                },
                default=str,
            )

        @tool
        def solve_slotting(max_moves: int, time_limit_s: float, objective: str) -> str:
            """Run the NVIDIA cuOpt constrained slotting solver.

            max_moves: how many relocations to allow, never above the operator's cap.
            time_limit_s: solver budget in seconds, at most 60.
            objective: one short sentence on what this pass should prioritise.

            Returns the moves found and what they would buy, including how much
            addressable headroom would remain afterwards. Nothing is applied.
            """
            if not agent.cuopt:
                raise RuntimeError("CUOPT_URL is not configured; there is no solver to call.")
            rounds = agent._run.setdefault("rounds", [])
            if len(rounds) >= MAX_SOLVE_ROUNDS:
                return json.dumps({"error": f"solve round limit of {MAX_SOLVE_ROUNDS} reached; finish and create the approval."})
            if agent._run.get("solver_failures", 0) >= MAX_SOLVER_FAILURES:
                return json.dumps({
                    "error": f"the solver failed {MAX_SOLVER_FAILURES} times in a row and is not usable right now",
                    "advice": "Stop solving and report that no plan could be produced. Do not call this tool again.",
                })

            cap = int(constraints.get("max_moves", 30))
            requested = int(max_moves)
            moves_allowed = max(1, min(requested, cap))
            budget = max(1.0, min(float(time_limit_s), float(MAX_SOLVER_SECONDS)))

            # cuOpt is deterministic for a given move count, so a repeat with a
            # reworded objective would burn a round to reproduce a known answer.
            previous = next((r for r in rounds if r["max_moves"] == moves_allowed), None)
            if previous is not None:
                return json.dumps(
                    {
                        "repeat_of_round": previous["round"],
                        "note": (
                            f"max_moves={moves_allowed} was already solved in round "
                            f"{previous['round']} and the solver is deterministic, so the result is "
                            "identical. Try a different move count or finish."
                        ),
                        "moves_proposed": len(previous["moves"]),
                        "kpis_after": previous["kpis_after"],
                        "headroom_after_metre_picks": previous["headroom_after"]["headroom_metre_picks"],
                    },
                    default=str,
                )

            source = agent._run.get("source") or agent._source_data()
            before = agent._run.get("measured") or agent._measure(source, constraints)
            round_constraints = dict(constraints)
            round_constraints["max_moves"] = moves_allowed

            agent._authorize("solve_slotting", actor)
            # Attempts are numbered, not just successes: a failed solve still has
            # to be distinguishable in the trace and on screen. Repeats rejected
            # above never reach here, so they leave no gap in the numbering.
            attempt = agent._run.get("solve_attempts", 0) + 1
            agent._run["solve_attempts"] = attempt
            agent._emit(
                "solve_started",
                {"round": attempt, "max_moves": moves_allowed, "time_limit_s": budget, "objective": objective},
            )

            problem = {
                "goal": agent._run.get("goal", ""),
                "planning_horizon_days": 7,
                "constraints": round_constraints,
                "objective": objective,
                "time_limit_s": budget,
                "analysis": agent._run.get("specialist_analysis", {}),
                "source_data": source,
                "required_output": {
                    "moves": [
                        "sku_id", "from_slot", "to_slot", "day", "window", "reason",
                        "benefit_hours_per_day", "labor_minutes", "confidence",
                    ],
                    "metrics": [
                        "travel_reduction_pct", "replenishment_reduction_pct",
                        "constraint_violations", "plan_value",
                    ],
                },
            }
            started = time.perf_counter()
            try:
                solution = agent.cuopt.solve_slotting(problem)
            except ServiceError as error:
                # Running out of solver budget is a fact the orchestrator can act
                # on; ending the whole run over it wastes everything before it.
                failures = agent._run.get("solver_failures", 0) + 1
                agent._run["solver_failures"] = failures
                agent._emit("solve_failed", {"round": attempt, "time_limit_s": budget, "error": str(error)})
                if failures >= MAX_SOLVER_FAILURES:
                    return json.dumps({
                        "error": f"the solver has now failed {failures} times in a row",
                        "detail": str(error)[:300],
                        "advice": "Stop solving and report that no plan could be produced for these constraints.",
                    })
                return json.dumps(
                    {
                        "error": "the solver returned no usable solution",
                        "detail": str(error)[:300],
                        "time_limit_s_used": budget,
                        "advice": (
                            f"cuOpt found nothing within {budget:.0f}s. Retry the same move count with a "
                            f"larger time_limit_s (up to {MAX_SOLVER_SECONDS}); a constrained layout often "
                            "needs the full budget."
                        ),
                    }
                )
            elapsed = round(time.perf_counter() - started, 1)
            agent._run["solver_failures"] = 0

            moves = list(solution.get("moves") or solution.get("recommendations") or [])
            projected = project_moves(source, moves)
            after = agent._measure(projected, constraints)

            record = {
                "round": attempt,
                "requested_max_moves": requested,
                "max_moves": moves_allowed,
                "time_limit_s": budget,
                "objective": objective,
                "solver_seconds": elapsed,
                "moves": moves,
                "solution": solution,
                "kpis_before": before["kpis"],
                "kpis_after": after["kpis"],
                "headroom_before": before["headroom"],
                "headroom_after": after["headroom"],
            }
            rounds.append(record)
            agent._run["best"] = max(rounds, key=lambda r: r["headroom_before"]["headroom_metre_picks"] - r["headroom_after"]["headroom_metre_picks"])
            agent._emit("solve_finished", {k: record[k] for k in ("round", "max_moves", "time_limit_s", "solver_seconds", "kpis_after", "headroom_after")})

            return json.dumps(
                {
                    "round": record["round"],
                    "rounds_remaining": MAX_SOLVE_ROUNDS - len(rounds),
                    "moves_proposed": len(moves),
                    "solver_seconds": elapsed,
                    "kpis_before": before["kpis"],
                    "kpis_after": after["kpis"],
                    "headroom_before_metre_picks": before["headroom"]["headroom_metre_picks"],
                    "headroom_after_metre_picks": after["headroom"]["headroom_metre_picks"],
                    "headroom_after_pct": after["headroom"]["headroom_pct"],
                    "constraint_violations": solution.get("metrics", {}).get("constraint_violations"),
                    "note": "Nothing has been applied. An operator approves these moves separately.",
                },
                default=str,
            )

        @tool
        def create_approval(rationale: str, round_number: int) -> str:
            """Raise the pending human approval for one specific solve round.

            round_number: which solve round's moves to put forward. This must be the
            round you actually recommend, because it is what the operator will see.
            rationale: what changes, what it buys, and what you chose not to do.
            """
            rounds = agent._run.get("rounds") or []
            chosen = next((r for r in rounds if r["round"] == int(round_number)), None)
            if chosen is None:
                return json.dumps(
                    {
                        "error": f"round {round_number} does not exist",
                        "available_rounds": [r["round"] for r in rounds] or "none; call solve_slotting first",
                    }
                )
            agent._authorize("create_approval", actor)
            agent._guard("output", {"moves": chosen["moves"], "kpis_after": chosen["kpis_after"]})
            # A revised recommendation must retire the earlier one, or the store
            # keeps two PENDING approvals and an operator can sign off the plan
            # the orchestrator has already moved on from.
            previous = agent._run.get("approval")
            if previous and previous.get("approval_id"):
                agent.approvals.decide(
                    previous["approval_id"],
                    "orchestrator",
                    False,
                    f"superseded by the round {chosen['round']} recommendation",
                )
            validation = {
                "openshell_allowed": True,
                "guardrails_allowed": True,
                "policy_id": "nemo-guardrails",
                "status": "PASSED",
                "rationale": rationale,
                "approved_round": chosen["round"],
            }
            approval = agent.approvals.create(chosen["moves"], actor, validation)
            agent._run["approval"] = approval
            agent._run["validation"] = validation
            agent._run["chosen"] = chosen
            agent._emit("approval_created", {"approval": approval, "round": chosen["round"]})
            return json.dumps(
                {
                    "approval_id": approval.get("approval_id"),
                    "round": chosen["round"],
                    "moves": len(chosen["moves"]),
                    "status": approval.get("status"),
                },
                default=str,
            )

        return [read_warehouse_state, solve_slotting, create_approval]

    # ------------------------------------------------------------------- build

    def _subagents(self, model) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "description": brief,
                "system_prompt": (
                    f"{brief}\n\nYou are given an aggregated warehouse summary and measured KPIs, "
                    "not raw records. Answer the question you are asked in at most six sentences. "
                    "Every number you state must come from the data you were given. "
                    "State what you looked at, what you concluded, and which constraints you think bind. "
                    "You cannot request more data and nobody will answer a question back, so never ask "
                    "one: if something you would like is missing, say what you can conclude without it "
                    "and name the gap."
                ),
                "model": model,
            }
            for name, brief in SPECIALIST_BRIEFS.items()
        ]

    def build(self, actor: str, constraints: Dict[str, Any], goal: str, site: str = "the site"):
        from deepagents import create_deep_agent

        supervisor = supervisor_model(self.config)
        # A subagent answers inside a tool call, so its turns never appear in the
        # stream run() reads; a callback is the only place to see their tokens.
        specialist = specialist_model(self.config, callbacks=[UsageRecorder(self._record_usage)])
        prompt = ORCHESTRATOR_PROMPT.format(
            site=site,
            goal=goal,
            constraints=json.dumps(constraints, indent=2, default=str),
            max_moves=int(constraints.get("max_moves", 30)),
            max_solver_seconds=MAX_SOLVER_SECONDS,
        )
        return create_deep_agent(
            model=supervisor,
            tools=self._build_tools(actor, constraints),
            system_prompt=prompt,
            subagents=self._subagents(specialist),
        )

    # --------------------------------------------------------------------- run

    def run(
        self,
        business_goal: str,
        actor: str,
        constraints: Dict[str, Any] | None = None,
        site: str = "the site",
    ) -> Dict[str, Any]:
        constraints = dict(constraints or {})
        with self._lock:
            self._run = {"goal": business_goal, "actor": actor, "constraints": constraints, "rounds": []}
            self._authorize("llm.supervisor", actor)
            # The specialists are a second model and a second egress path; they
            # were being used on the supervisor's authorisation alone.
            self._authorize("llm.subagent", actor)
            self._guard("input", {"goal": business_goal, "constraints": constraints})

            agent = self.build(actor, constraints, business_goal, site)
            self._run["harness"] = profile_report(supervisor_model(self.config))
            # The profile is static config, so the console can show it for the
            # whole run rather than only once the run returns.
            self._emit("harness", self._run["harness"])
            started = time.perf_counter()
            messages = self._stream(agent, business_goal)
            self._run["duration_ms"] = round((time.perf_counter() - started) * 1000)
            final = messages[-1] if messages else None
            self._run["narrative"] = getattr(final, "content", "") or ""
            self._run["messages"] = messages
            return self._run

    def _stream(self, agent, business_goal: str) -> List[Any]:
        """Run the agent, surfacing each step as it happens.

        The UI shows the orchestrator delegating and re-solving live, so the run
        cannot be a single blocking invoke that only reports once it is over.
        """
        messages: List[Any] = []
        pending_delegations: Dict[str, Dict[str, Any]] = {}
        for chunk in agent.stream(
            {"messages": [{"role": "user", "content": business_goal}]},
            {"recursion_limit": 120},
            stream_mode="updates",
        ):
            for update in chunk.values():
                if not isinstance(update, dict):
                    continue
                for message in update.get("messages") or []:
                    messages.append(message)
                    self._on_message(message, pending_delegations)
        return messages

    def _on_message(self, message: Any, pending: Dict[str, Dict[str, Any]]) -> None:
        calls = getattr(message, "tool_calls", None) or []
        extra = getattr(message, "additional_kwargs", None) or {}
        # Nemotron puts its chain of thought here rather than in content.
        reasoning = extra.get("reasoning_content") or ""
        content = getattr(message, "content", "") or ""
        if not isinstance(content, str):
            content = ""

        usage = getattr(message, "usage_metadata", None)
        if usage:
            meta = getattr(message, "response_metadata", None) or {}
            self._record_usage(
                str(meta.get("model_name") or meta.get("model") or "unknown"),
                int(usage.get("input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
            )

        if calls:
            if reasoning or content.strip():
                self._emit("orchestrator_thinking", {"reasoning": reasoning, "content": content.strip()})
            for call in calls:
                name = call.get("name", "")
                args = call.get("args") or {}
                if name == "task":
                    record = {
                        "id": call.get("id", ""),
                        "name": str(args.get("subagent_type") or "specialist"),
                        "question": str(args.get("description") or ""),
                    }
                    pending[record["id"]] = record
                    self._run.setdefault("delegations", []).append(record)
                    self._emit("delegation_started", dict(record))
                else:
                    self._emit("tool_started", {"tool": name, "args": args})
            return

        if type(message).__name__ == "ToolMessage":
            call_id = getattr(message, "tool_call_id", "")
            if getattr(message, "name", "") == "task":
                record = pending.pop(call_id, None) or {"id": call_id, "name": "specialist", "question": ""}
                record["answer"] = content
                self._emit("delegation_finished", dict(record))
            return

        if reasoning or content.strip():
            self._emit("orchestrator_thinking", {"reasoning": reasoning, "content": content.strip()})

    # The write path is unchanged: the orchestrator never touches the WMS.
    def write_back(self, approval_id: str, actor: str) -> Dict[str, Any]:
        decision = self.openshell.authorize("write_wms", actor, approval_id)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        approval = self.approvals.require_approved(approval_id)
        guardrail = self.guardrails.validate(
            "tool_call",
            {"action": "wms_write", "approval_id": approval_id, "moves": approval["moves"]},
        )
        if not guardrail.allowed:
            raise PermissionError(guardrail.reason)
        return self.wms.apply_approved_moves(approval["moves"], approval_id)
