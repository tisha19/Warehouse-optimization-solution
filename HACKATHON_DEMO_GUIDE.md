# Hackathon Live Demo Guide

## 1. Prepare the Environment

Open a terminal in the project:

```bash
cd "/Users/mainak.chatterjee/Library/CloudStorage/OneDrive-Personal/Carrier Journey/Projects/warehouse-optimization-platform"
source .venv/bin/activate
```

Optionally verify the solution:

```bash
python -m unittest discover -s tests -v
```

Expected result:

```text
Ran 9 tests
OK
```

## 2. Configure Real NVIDIA Services

Populate your local `.env` or export the required variables:

```bash
export NIM_BASE_URL="https://your-nim-endpoint/v1"
export NIM_MODEL="your-nemotron-model"
export NIM_API_KEY="your-api-key"

export CUOPT_URL="https://your-cuopt-endpoint"
export NEMO_GUARDRAILS_URL="https://your-guardrails-endpoint"
export OPENSHELL_URL="https://your-openshell-endpoint"
```

Do not show credentials during the presentation.

The showcase UI labels its built-in warehouse data as **Scenario Replay**. NVIDIA and cuOpt services are not mocked.

## 3. Start the Showcase

```bash
python showcase_server.py --port 8080
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080).

Use a browser width above `1100px` so the move-cap control and intelligence sidebar remain visible.

## 4. Introduce the Problem

Suggested narration:

> Warehouse demand changes faster than static slotting plans. Traditional tools tell planners where a SKU belongs, but not when to move it, how much disruption it causes, or whether the move can be executed safely.

Then introduce Shiftwise:

> Shiftwise converts changing demand into a safe, explainable, multi-day move plan with human approval before WMS execution.

## 5. Explain the Initial Scenario

Point out the planner instruction:

```text
Prepare a seven-day promotion plan. Keep moves below 10,
lock cold-chain inventory, and prioritize picker travel.
```

Explain the scenario:

- Sparkling Water demand has increased by 40%.
- The SKU is currently far from dispatch.
- Cold-chain inventory must remain locked.
- Moves must occur during low-volume shifts.
- The planner permits no more than ten moves.

## 6. Show the Initial Plan

Highlight the top KPI band:

- **18.5%** picker-travel reduction
- **12%** fewer replenishments
- **74 minutes** of relocation work
- **0** constraint violations

Emphasize that this is not merely a slot assignment. It is a seven-day execution plan.

## 7. Show the Move Calendar

Use the **Seven-day move calendar** to demonstrate:

- Moves scheduled across multiple days
- Specific shift windows
- Travel, replenishment, and safety move categories
- Empty days where disruption is intentionally avoided

Suggested narration:

> The system determines not only where inventory should go, but when the warehouse can safely execute each move.

## 8. Explain the Top Recommendation

Select or point to:

```text
Sparkling Water 12pk
B-12 -> A-03
Tonight, 20:00-20:12
```

Then read the **Nemotron rationale**:

- Demand rises by 40%.
- The new location is 62 metres closer to dispatch.
- Expected benefit is 2.1 picker-hours per day.
- Relocation requires only 12 minutes.
- Confidence is 96%.

Explain the technical separation:

> Nemotron explains the decision, but it does not select the final location. cuOpt produces the constrained plan, and deterministic validation checks it.

## 9. Show the Move Manifest

Scroll to the **WMS-ready Move Manifest**.

Highlight that each move contains:

- Priority
- Execution date and shift window
- SKU
- Source and destination
- Decision evidence
- Expected value
- Labor requirement
- Approval status

Suggested narration:

> The output is immediately actionable. It can become a WMS task after planner approval.

## 10. Show Warehouse Spatial Intelligence

Use the **Warehouse pressure map** to explain:

- Zone A is the forward-pick area.
- Zone pressure and utilization vary.
- Distance from dispatch is considered.
- The optimizer balances proximity against capacity and operational constraints.

## 11. Show the Multi-Agent Pipeline

Use the **DeepAgent trace** panel:

1. Demand Agent detects the promotion uplift.
2. Inventory Agent identifies replenishment risks.
3. Warehouse Agent finds feasible locations.
4. Orchestrator combines evidence and invokes optimization.

Then show the production service panel:

- NVIDIA NIM
- NeMo Retriever
- NVIDIA cuOpt
- NeMo Guardrails
- OpenShell

A service displays **configured** when its environment URL exists.

## 12. Demonstrate Planner Control

This is the main demo moment.

Change the planner instruction to:

```text
Lock Sparkling Water in B-12 and keep the plan under 6 moves
```

Set **Move cap** to `6`, then click the arrow button in the planner instruction field.

Expected revised result:

- Move count changes from **8 to 6**.
- Travel reduction changes from **18.5% to 12.8%**.
- Constraint violations remain **0**.
- Sparkling Water disappears from the move manifest.
- The explanation states that the SKU remains locked.

Suggested narration:

> The planner has overridden the highest-value move. The system respects that decision, re-optimizes the remaining plan, and explicitly quantifies the lost benefit.

## 13. Demonstrate Approval

In the Move Manifest, click **Review** on a move.

The move changes to:

```text
Approved
```

Explain:

> Recommendations are never written directly to the WMS. Each move requires human approval, Guardrails validation, and OpenShell authorization.

The top **Review plan** button demonstrates opening the overall plan for supervisor review.

## 14. Explain Production Execution

Describe the controlled production path:

```text
Agent proposes
    -> cuOpt finds a feasible plan
    -> deterministic validators check constraints
    -> NeMo Guardrails validates the output
    -> OpenShell checks permissions
    -> planner approves
    -> approved tasks are written to the WMS
```

## 15. Close With the USP

End with:

> Shiftwise is not another static slotting optimizer. It is a disruption-aware, multi-period warehouse decision copilot that tells planners which SKUs to move, where, when, in what order, and why, while retaining human control over execution.

## Recommended Demo Timing

| Time | Segment |
|---|---|
| 0:00-0:30 | Warehouse problem and product USP |
| 0:30-1:15 | Initial scenario and KPI results |
| 1:15-2:00 | Seven-day calendar and move manifest |
| 2:00-2:40 | Nemotron explanation and agent trace |
| 2:40-3:30 | Lock Sparkling Water and re-optimize |
| 3:30-4:00 | Approval and WMS execution controls |
| 4:00-4:30 | NVIDIA architecture and closing value |
