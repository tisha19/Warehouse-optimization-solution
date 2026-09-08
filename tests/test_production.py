"""Offline tests for production governance and evaluation boundaries."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mocks.enterprise_services import MockServiceState
from services.config import ProductionConfig
from agents.workflow import ProductionWarehouseWorkflow
from showcase_server import Scenario
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

    def test_showcase_replan_exposes_tradeoff(self):
        scenario = Scenario()
        baseline = scenario.state()
        revised = scenario.replan({"max_moves": 6, "lock_sku": "SKU-100"})
        self.assertEqual(baseline["metrics"]["travel_reduction"], 18.5)
        self.assertEqual(revised["metrics"]["move_count"], 6)
        self.assertEqual(revised["metrics"]["travel_reduction"], 12.8)
        self.assertNotIn("SKU-100", {move["code"] for move in revised["moves"]})
        self.assertEqual(revised["metrics"]["violations"], 0)

    def test_showcase_move_approval(self):
        scenario = Scenario()
        move_id = scenario.state()["moves"][0]["id"]
        updated = scenario.approve(move_id)
        self.assertEqual(updated["approval_status"], "APPROVED")
        self.assertTrue(all(move["status"] == "Approved" for move in updated["moves"]))

    @patch("agents.workflow.CuOptClient.solve_slotting", side_effect=RuntimeError("cuOpt unavailable"))
    @patch("agents.workflow.NIMClient.chat", side_effect=RuntimeError("NIM unavailable"))
    def test_workflow_keeps_renderable_data_with_unavailable_live_services(self, *_):
        state = MockServiceState(seed=7)
        controller = Scenario()
        controller.enterprise = state
        view = controller.run("Reduce travel and keep cold-chain locked", {"max_moves": 6, "locked_skus": ["SKU-100"]})
        self.assertEqual(view["metrics"]["move_count"], 6)
        self.assertTrue(view["moves"])
        self.assertEqual(view["metrics"]["violations"], 0)
        self.assertNotIn("SKU-100", {move["code"] for move in view["moves"]})

if __name__ == "__main__":
    unittest.main()
