"""Offline tests for production governance and evaluation boundaries."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mocks.enterprise_services import MockServiceState
from services.config import ProductionConfig
from services.nvidia import PolicyDecision
from showcase.controller import ShowcaseController
from showcase.evaluation import build_scorecard
from showcase.kpis import slotting_headroom, warehouse_kpis
from tools.evaluation import AgentCase, AgentEvaluator
from tools.evaluation_suite import AGENT_NAMES, evaluate_all_agents
from tools.governance import ApprovalWorkflow, OpenShellPolicy
from tools.harness import DEFAULT_REGRESSION_CASES, run_regression_suite


class ProductionHarnessTests(unittest.TestCase):
    def test_regression_suite(self):
        report = run_regression_suite(DEFAULT_REGRESSION_CASES)
        self.assertTrue(report["passed"])

    def test_approval_is_required_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = ApprovalWorkflow(str(Path(directory) / "approvals.json"))
            approval = workflow.create([{"sku_id": "SKU1", "to_slot": "A001"}], "planner", {"status": "PASSED"})
            with self.assertRaises(PermissionError):
                workflow.require_approved(approval["approval_id"])
            workflow.decide(approval["approval_id"], "supervisor", True, "Validated")
            self.assertEqual(workflow.require_approved(approval["approval_id"])["status"], "APPROVED")

    def test_openshell_blocks_unknown_tools(self):
        policy = OpenShellPolicy(ProductionConfig())
        self.assertFalse(policy.authorize("delete_inventory", "planner").allowed)
        self.assertFalse(policy.authorize("write_wms", "planner").allowed)

    def test_all_agent_scores_are_reportable(self):
        evaluator = AgentEvaluator()
        cases = [AgentCase("schema", {"value": 1}, {"required_fields": ["status"]})]
        report = evaluator.evaluate_suite({"demand": lambda _: {"status": "ok"}, "inventory": lambda _: {"status": "ok"}}, {"demand": cases, "inventory": cases})
        self.assertEqual(set(report["summary"]["agents"]), {"demand", "inventory"})
        self.assertEqual(report["summary"]["overall_pass_rate_pct"], 100.0)

    def test_evaluation_configuration_matches_logical_agents(self):
        expected_agents = ("demand", "inventory", "warehouse", "orchestrator")
        self.assertEqual(AGENT_NAMES, expected_agents)
        runners = {name: lambda _: {"status": "ok"} for name in expected_agents}
        cases = [AgentCase("schema", {}, {"required_fields": ["status"]})]
        report = evaluate_all_agents(runners, cases)
        self.assertEqual(set(report["summary"]["agents"]), set(expected_agents))

    def test_evaluation_configuration_rejects_missing_agent(self):
        with self.assertRaisesRegex(ValueError, "orchestrator"):
            evaluate_all_agents({name: lambda _: {"status": "ok"} for name in AGENT_NAMES if name != "orchestrator"}, [])

    def test_mock_enterprise_data_contracts(self):
        state = MockServiceState(seed=7)
        self.assertEqual(len(state.sku_master()["sku_master"]), 100)
        self.assertEqual(len(state.snapshot()["warehouse_layout"]), 498)
        self.assertEqual(len(state.forecast(14)["forecast"]), 1400)

    def test_applying_moves_relocates_stock(self):
        state = MockServiceState(seed=7)
        occupancy = {row["sku_id"]: row["slot_id"] for row in state.snapshot()["slot_occupancy"]}
        sku_id, origin = next(iter(occupancy.items()))
        free = next(slot["slot_id"] for slot in state.snapshot()["warehouse_layout"] if slot["slot_id"] not in set(occupancy.values()))
        state.apply_moves([{"sku_id": sku_id, "from_slot": origin, "to_slot": free}])
        moved = {row["sku_id"]: row["slot_id"] for row in state.snapshot()["slot_occupancy"]}
        self.assertEqual(moved[sku_id], free)

    def test_kpis_measure_the_current_snapshot(self):
        state = MockServiceState(seed=7)
        source = {
            "wms": state.snapshot(),
            "erp": {"sku_master": state.sku_master(), "inbound": state.inbound()},
            "forecast": state.forecast(14),
        }
        kpis = warehouse_kpis(source)
        self.assertGreater(kpis["daily_travel_km"], 0)
        self.assertGreater(kpis["avg_distance_per_pick_m"], 0)
        self.assertLessEqual(kpis["forward_pick_coverage_pct"], 100)
        # Headroom is the measured signal the orchestrator reasons from.
        self.assertGreater(slotting_headroom(source, {})["headroom_metre_picks"], 0)

    def _offline_controller(self, directory: str) -> ShowcaseController:
        """A controller whose workflow cannot reach the live services."""
        approvals = ApprovalWorkflow(str(Path(directory) / "approvals.json"))

        class UnreachableWorkflow:
            def __init__(self) -> None:
                self.approvals = approvals
                self.openshell = SimpleNamespace(authorize=lambda *_args, **_kwargs: PolicyDecision(True, "granted in test", "test"))

            def run(self, *_args, **_kwargs):
                raise RuntimeError("live services unavailable")

        return ShowcaseController(workflow_factory=lambda **_: UnreachableWorkflow())

    def test_dashboard_needs_no_live_services(self):
        with tempfile.TemporaryDirectory() as directory:
            dashboard = self._offline_controller(directory).dashboard()
        self.assertEqual(dashboard["counts"]["skus"], 100)
        self.assertGreater(dashboard["kpis"]["daily_travel_km"], 0)
        self.assertEqual(len(dashboard["zones"]), 6)
        # The interpretation is a model call, so it starts pending rather than
        # claiming there is nothing wrong.
        self.assertIn(dashboard["analysis"]["status"], ("pending", "ready", "failed"))

    def test_failed_run_reports_the_error_and_produces_no_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._offline_controller(directory)
            controller._execute()
            run = controller.run()
        self.assertEqual(run["status"], "FAILED")
        self.assertIn("live services unavailable", run["error"])
        self.assertEqual(run["moves"], [])

    def test_commit_refuses_without_a_completed_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._offline_controller(directory)
            with self.assertRaisesRegex(RuntimeError, "no completed plan"):
                controller.commit()

    def test_executive_scorecard_has_weighted_categories(self):
        run = {
            "id": "run-1",
            "status": "COMPLETE",
            "summary": {"metrics": {"travel_reduction_pct": 18.0, "cost_reduction_pct": 12.0, "constraint_violations": 0, "plan_value": 88.0}},
            "orchestrator": {"duration_ms": 9200, "thinking": ["travel is the binding constraint"], "narrative": "clear plan"},
            "rounds": [
                {
                    "round": 1,
                    "status": "done",
                    "headroom_before": {"headroom_metre_picks": 40},
                    "headroom_after": {"headroom_metre_picks": 18},
                }
            ],
            "chosen_round": 1,
            "moves": [{"labor_minutes": 24}],
            "constraints": {"labour_minutes_per_window": 240, "max_moves": 30},
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
            "openshell": [],
            "validation": {"status": "PASSED"},
            "error": None,
        }

        scorecard = build_scorecard(run)

        self.assertEqual(len(scorecard["categories"]), 6)
        self.assertEqual(sum(item["weight"] for item in scorecard["weights"]), 100)
        self.assertGreaterEqual(scorecard["overall_score"], 0)
        self.assertLessEqual(scorecard["overall_score"], 100)
        resource_metric = next(
            metric for category in scorecard["categories"] if category["key"] == "efficiency" for metric in category["metrics"] if metric["label"] == "Resource utilization"
        )
        self.assertIsNotNone(resource_metric["value"])
        self.assertGreater(resource_metric["value"], 0)

    def test_completed_run_persists_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = self._offline_controller(directory)

            class CompletedWorkflow:
                def __init__(self) -> None:
                    self.approvals = controller.workflow.approvals
                    self.openshell = SimpleNamespace(authorize=lambda *_args, **_kwargs: PolicyDecision(True, "granted in test", "test"))

                def run(self, *_args, **_kwargs):
                    return {
                        "chosen": {
                            "round": 1,
                            "moves": [{"sku_id": "SKU1", "from_slot": "A001", "to_slot": "A002", "labor_minutes": 24}],
                            "solution": {
                                "headline": "Reduce travel",
                                "explanation": "The forward pick face is too far from demand.",
                                "metrics": {"travel_reduction_pct": 18.0, "cost_reduction_pct": 12.0, "constraint_violations": 0, "plan_value": 88.0},
                            },
                        },
                        "rounds": [
                            {
                                "round": 1,
                                "max_moves": 5,
                                "time_limit_s": 60,
                                "solver_seconds": 11.2,
                                "moves": [{"sku_id": "SKU1", "from_slot": "A001", "to_slot": "A002", "labor_minutes": 24}],
                                "kpis_before": {"avg_distance_per_pick_m": 12.0, "forward_pick_coverage_pct": 62.0, "daily_travel_km": 80.0},
                                "kpis_after": {"avg_distance_per_pick_m": 9.8, "forward_pick_coverage_pct": 70.0, "daily_travel_km": 66.0},
                                "headroom_before": {"headroom_metre_picks": 40},
                                "headroom_after": {"headroom_metre_picks": 18},
                            }
                        ],
                        "harness": {"attached": True, "middleware": [], "prompt_suffix_chars": 0},
                        "narrative": "clear plan",
                        "usage": {"calls": 3, "prompt_tokens": 1200, "completion_tokens": 300, "by_model": {}},
                        "approval": {"approval_id": "APR-1", "status": "PENDING"},
                        "validation": {"status": "PASSED"},
                    }

            controller.workflow = CompletedWorkflow()
            controller._execute()
            run = controller.run()

        self.assertEqual(run["status"], "COMPLETE")
        self.assertIsNotNone(run["evaluation"])
        self.assertIn(run["evaluation"]["strongest_category"], {item["key"] for item in run["evaluation"]["categories"]})


if __name__ == "__main__":
    unittest.main()
