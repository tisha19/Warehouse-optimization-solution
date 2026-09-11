"""State machine behind the WarehouseIQ UI.

Four screens, one rule: nothing is shown that a live service did not produce.
There is no placeholder plan and no fallback. If the NIM, cuOpt, NeMo
Guardrails or the OpenShell governor cannot do its part, the run fails with the
reason and the UI reports it.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from agents.deep_workflow import WarehouseDeepAgent
from agents.digest import warehouse_digest
from mocks.enterprise_adapters import SyntheticERPAdapter, SyntheticForecastAdapter, SyntheticWMSAdapter
from mocks.enterprise_services import MockServiceState, resolve_seed
from services.config import ProductionConfig
from services.harness_profile import ANALYSIS_MAX_TOKENS, specialist_model
from showcase.analysis import analyse_slotting
from showcase.demand import daily_picks, demand_signal
from showcase.kpis import slotting_headroom, warehouse_kpis

ACTOR = "warehouse-planner"
SITE = "DC-07 / North distribution centre"
GOAL = (
    "Reduce picker travel over the next seven days. Stay within the move cap, keep cold-chain stock "
    "where it is, and prioritise the highest-demand lines."
)
# write_wms is per_call, so the governor holds the write until an operator answers.
WRITE_APPROVAL_WAIT_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ShowcaseController:
    def __init__(self, seed: int | None = None, workflow_factory: Callable[..., Any] = WarehouseDeepAgent):
        self.workflow_factory = workflow_factory
        self.config = ProductionConfig.from_env()
        self.goal = GOAL
        self.constraints: Dict[str, Any] = {"max_moves": 30, "locked_skus": [], "cold_chain_locked": True, "labour_minutes_per_window": 240, "execution_windows": ["low-volume shifts"]}
        self._lock = threading.Lock()
        self._load(resolve_seed(seed))

    def _load(self, seed: int) -> None:
        self.seed = seed
        self.enterprise = MockServiceState(seed)
        self.run_state = self._idle_run()
        self.decisions = {}
        self.commit_history = []
        self.commit_state = {"status": "IDLE", "service": "write_wms", "error": None, "commit": None}
        self.baseline_kpis = None
        self._analysis: Dict[str, Any] = {"key": None, "status": "pending", "error": None, "problems": []}
        self.workflow = self.workflow_factory(
            config=self.config,
            wms=SyntheticWMSAdapter(self.enterprise),
            erp=SyntheticERPAdapter(self.enterprise),
            forecast=SyntheticForecastAdapter(self.enterprise),
            on_event=self._on_workflow_event,
        )

    def reset(self) -> Dict[str, Any]:
        """Generate a different warehouse and forget everything about the last one."""
        with self._lock:
            self._load(random.SystemRandom().randrange(1, 2**31))
        return self.dashboard()

    # ---------------------------------------------------------------- dashboard

    def _source_data(self) -> Dict[str, Any]:
        return {
            "wms": self.enterprise.snapshot(),
            "erp": {"sku_master": self.enterprise.sku_master(), "inbound": self.enterprise.inbound()},
            "forecast": self.enterprise.forecast(14),
        }

    def _ensure_analysis(self, source: Mapping[str, Any], kpis: Mapping[str, Any]) -> None:
        """Interpret the current state once, off the request thread.

        The dashboard is polled continuously; asking a model on every poll would
        be both slow and wasteful, so the answer is cached against the state it
        describes and only recomputed when that state changes.
        """
        key = (self.seed, len(self.commit_history), json.dumps(self.constraints, sort_keys=True, default=str))
        if self._analysis.get("key") == key and self._analysis.get("status") != "failed":
            return
        if self._analysis.get("inflight") == key:
            return

        headroom = slotting_headroom(source, self.constraints)
        zones = [
            {"zone": z["zone"], "slots": z["slots"], "free": z["free"], "avg_distance_m": z["avg_distance_m"]}
            for z in warehouse_digest(source, self.constraints).get("zones", [])
        ]
        self._analysis = {"key": self._analysis.get("key"), "inflight": key,
                          "status": "pending", "error": None,
                          "problems": self._analysis.get("problems", [])}

        def work() -> None:
            try:
                problems = analyse_slotting(
                    specialist_model(self.config, max_tokens=ANALYSIS_MAX_TOKENS),
                    kpis,
                    headroom,
                    self.constraints,
                    zones,
                )
                result = {"key": key, "status": "ready", "error": None, "problems": problems}
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never hidden
                result = {"key": None, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "problems": []}
            with self._lock:
                self._analysis = result

        threading.Thread(target=work, daemon=True).start()

    def dashboard(self) -> Dict[str, Any]:
        source = self._source_data()
        kpis = warehouse_kpis(source)
        if self.baseline_kpis is None:
            self.baseline_kpis = dict(kpis)
        self._ensure_analysis(source, kpis)
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
            "site": SITE,
            "kpis": kpis,
            "baseline": self.baseline_kpis,
            "problems": self._analysis.get("problems", []),
            "analysis": {
                "status": self._analysis.get("status", "pending"),
                "error": self._analysis.get("error"),
                "model": self.config.nim_subagent_model or self.config.nim_model,
            },
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
            "last_commit": self.commit_history[-1] if self.commit_history else None,
            "relocated_total": sum(int(c.get("relocated", 0)) for c in self.commit_history),
            "dataset_seed": self.seed,
            "run": {"id": self.run_state.get("id"), "status": self.run_state.get("status")},
            "generated_at": _now(),
        }

    def layout(self) -> Dict[str, Any]:
        """Slot geometry and what occupies each one, for the warehouse map."""
        source = self._source_data()
        wms = source["wms"]
        sku_by_id = {str(row.get("sku_id")): row for row in source["erp"]["sku_master"]["sku_master"]}
        picks = daily_picks(source)
        occupant: Dict[str, Dict[str, Any]] = {}
        for row in wms["slot_occupancy"]:
            slot_id = str(row["slot_id"])
            sku_id = str(row["sku_id"])
            sku = sku_by_id.get(sku_id, {})
            occupant[slot_id] = {
                "sku_id": sku_id,
                "product_name": sku.get("product_name", sku_id),
                "abc_class": sku.get("abc_class", ""),
                "quantity": int(row.get("quantity", 0)),
                "picks_per_day": round(picks.get(sku_id, 0.0)),
            }
        slots = [
            {
                "slot_id": str(row["slot_id"]),
                "zone": chr(64 + int(row["zone_id"])),
                "zone_id": int(row["zone_id"]),
                "aisle": int(row.get("aisle", 1)),
                "bay": int(row.get("bay", 1)),
                "level": int(row.get("level", 1)),
                "distance_m": float(row.get("distance_to_picking_m", 0.0)),
                "status": str(row.get("operational_status", "ACTIVE")),
                "temperature_controlled": bool(row.get("temperature_controlled")),
                "occupant": occupant.get(str(row["slot_id"])),
            }
            for row in wms["warehouse_layout"]
        ]
        return {
            "site": SITE,
            "slots": slots,
            "aisles_per_zone": max((slot["aisle"] for slot in slots), default=1),
            "bays_per_aisle": max((slot["bay"] for slot in slots), default=1),
            "levels_per_bay": max((slot["level"] for slot in slots), default=1),
            "forward_pick_zone": "A",
            "moves": self.run_state.get("moves", []),
        }

    def demand(self) -> Dict[str, Any]:
        return demand_signal(self._source_data())

    def set_constraints(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Planner-editable limits; the next run hands these to cuOpt."""
        if "max_moves" in payload:
            self.constraints["max_moves"] = max(1, min(120, int(payload["max_moves"])))
        if "cold_chain_locked" in payload:
            self.constraints["cold_chain_locked"] = bool(payload["cold_chain_locked"])
        if "locked_skus" in payload:
            self.constraints["locked_skus"] = sorted({str(sku) for sku in payload["locked_skus"]})
        if "labour_minutes_per_window" in payload:
            self.constraints["labour_minutes_per_window"] = max(30, min(960, int(payload["labour_minutes_per_window"])))
        return dict(self.constraints)

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
            "orchestrator": {
                "model": self.config.nim_supervisor_model,
                "status": "pending",
                "harness": {"attached": False, "middleware": [], "prompt_suffix_chars": 0},
                "thinking": [],
                "narrative": "",
            },
            "delegations": [],
            "rounds": [],
            "chosen_round": None,
            "guardrails": [],
            "openshell": [],
            "moves": [],
            "summary": None,
            "approval": None,
            "validation": None,
            "error": None,
            "halted_on": None,
            "started_at": None,
            "finished_at": None,
        }

    def _set_orchestrator(self, status: str) -> None:
        self.run_state["orchestrator"]["status"] = status

    def _on_workflow_event(self, kind: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            run = self.run_state
            if kind == "orchestrator_thinking":
                text = str(payload.get("reasoning") or payload.get("content") or "").strip()
                if text:
                    run["orchestrator"]["thinking"].append(text)
                self._set_orchestrator("running")
            elif kind == "state_read":
                run["measured"] = {"kpis": payload.get("kpis"), "problems": payload.get("problems")}
            elif kind == "delegation_started":
                run["delegations"].append({
                    "id": payload.get("id"),
                    "name": payload.get("name"),
                    "question": payload.get("question"),
                    "answer": "",
                    "status": "running",
                })
            elif kind == "delegation_finished":
                for record in run["delegations"]:
                    if record["id"] == payload.get("id"):
                        record["answer"] = payload.get("answer", "")
                        record["status"] = "done"
            elif kind == "solve_started":
                run["rounds"].append({
                    "round": payload.get("round"),
                    "max_moves": payload.get("max_moves"),
                    "time_limit_s": payload.get("time_limit_s"),
                    "objective": payload.get("objective"),
                    "status": "running",
                })
            elif kind == "solve_finished":
                for record in run["rounds"]:
                    if record["round"] == payload.get("round"):
                        record.update({
                            "status": "done",
                            "solver_seconds": payload.get("solver_seconds"),
                            "kpis_after": payload.get("kpis_after"),
                            "headroom_after": payload.get("headroom_after"),
                        })
            elif kind == "solve_failed":
                for record in run["rounds"]:
                    if record["round"] == payload.get("round"):
                        record.update({"status": "failed", "error": payload.get("error")})
            elif kind == "guardrail":
                run["guardrails"].append({**dict(payload), "at": _now()})
            elif kind == "openshell":
                run["openshell"].append({**dict(payload), "at": _now()})
            elif kind == "openshell_halt":
                run["status"] = "HALTED"
                run["halted_on"] = {"service": payload.get("service"), "reason": payload.get("reason")}
                self._set_orchestrator("waiting-for-approval")
            elif kind == "openshell_resumed":
                run["status"] = "RUNNING"
                run["halted_on"] = None
                self._set_orchestrator("running")
            elif kind == "approval_created":
                run["chosen_round"] = payload.get("round")

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
        threading.Thread(target=self._execute, daemon=True).start()
        return self.run()

    def _execute(self) -> None:
        try:
            state = self.workflow.run(self.goal, ACTOR, dict(self.constraints), site=SITE)
            with self._lock:
                self._finish(state)
        except Exception as exc:
            with self._lock:
                self.run_state["status"] = "FAILED"
                self.run_state["error"] = f"{type(exc).__name__}: {exc}"
                self.run_state["finished_at"] = _now()
                self._set_orchestrator("failed")
                for record in self.run_state["delegations"]:
                    if record["status"] == "running":
                        record["status"] = "failed"
                for record in self.run_state["rounds"]:
                    if record.get("status") == "running":
                        record["status"] = "failed"

    def _finish(self, state: Mapping[str, Any]) -> None:
        chosen = dict(state.get("chosen") or {})
        solution = dict(chosen.get("solution") or {})
        raw_moves = list(chosen.get("moves") or [])
        source = self._source_data()
        sku_by_id = {str(row.get("sku_id")): row for row in source.get("erp", {}).get("sku_master", {}).get("sku_master", [])}
        moves = [self._normalise_move(index, move, sku_by_id) for index, move in enumerate(raw_moves, 1)]
        self.decisions = {move["id"]: "pending" for move in moves}
        orchestrator = self.run_state["orchestrator"]
        orchestrator.update({
            "status": "done",
            "harness": dict(state.get("harness") or orchestrator["harness"]),
            "narrative": str(state.get("narrative") or ""),
            "duration_ms": state.get("duration_ms"),
        })
        # The solver detail only exists once the round is complete, so the rounds
        # recorded live are merged with what the run finally holds.
        by_round = {r.get("round"): r for r in state.get("rounds") or []}
        for record in self.run_state["rounds"]:
            full = by_round.get(record.get("round"))
            if not full:
                continue
            record.update({
                "moves": len(full.get("moves") or []),
                "kpis_before": full.get("kpis_before"),
                "kpis_after": full.get("kpis_after"),
                "headroom_before": full.get("headroom_before"),
                "headroom_after": full.get("headroom_after"),
            })
        self.run_state.update({
            "status": "COMPLETE",
            "finished_at": _now(),
            "moves": moves,
            "usage": dict(state.get("usage") or {}),
            "summary": {
                "headline": solution.get("headline"),
                "explanation": orchestrator["narrative"] or solution.get("explanation"),
                "metrics": solution.get("metrics") or {},
                "move_count": len(moves),
            },
            "approval": state.get("approval"),
            "validation": state.get("validation"),
            "chosen_round": chosen.get("round"),
        })

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
            "alternatives": list(raw.get("alternatives") or []),
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
            kpis_before = warehouse_kpis(self._source_data())
            result = self.workflow.wms.apply_approved_moves(payload, approval_id)
            record = {
                "at": _now(),
                "approval_id": approval_id,
                "moves": len(payload),
                "relocated": int(result.get("relocated", len(payload))),
                "rejected": sum(1 for value in self.decisions.values() if value == "rejected"),
                # Kept so the cockpit can draw what was actually executed once
                # the plan itself is gone.
                "applied": [dict(move) for move in approved],
                "kpis_before": kpis_before,
                "kpis_after": warehouse_kpis(self._source_data()),
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
        harness = dict(self.run_state.get("orchestrator", {}).get("harness") or {})
        usage = dict(self.run_state.get("usage") or {})
        delegations = len(self.run_state.get("delegations") or [])
        return {
            "models": [
                {
                    "role": "orchestrator",
                    "model": self.config.nim_supervisor_model,
                    "endpoint": self.config.nim_supervisor_base_url,
                    "calls": usage.get("calls", 0),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "harness_middleware": len(harness.get("middleware") or []),
                },
                {
                    "role": "specialists",
                    "model": self.config.nim_subagent_model or self.config.nim_model,
                    "endpoint": self.config.nim_subagent_base_url or self.config.nim_base_url,
                    "calls": delegations,
                    # Subagents run isolated, so their usage never reaches this process.
                    "prompt_tokens": None,
                    "completion_tokens": None,
                },
            ],
            "totals": {
                "calls": usage.get("calls", 0) + delegations,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            "solve_rounds": len(self.run_state.get("rounds") or []),
            "commits": len(self.commit_history),
        }

    def resolve_request(self, request_id: str, approve: bool) -> Dict[str, Any]:
        action = "approve" if approve else "deny"
        self._governor(f"/api/v1/requests/{request_id}/{action}", "POST", {"resolved_by": "warehouseiq-admin"})
        return self.openshell_overview()

    def revoke_grant(self, user: str, service: str) -> Dict[str, Any]:
        self._governor(f"/api/v1/grants/{user}/{service}", "DELETE")
        return self.openshell_overview()
