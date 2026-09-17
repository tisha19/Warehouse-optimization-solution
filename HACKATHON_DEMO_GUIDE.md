# WarehouseIQ Live Demo Guide

WarehouseIQ is a governed, multi-agent warehouse slotting copilot. Everything on screen
is produced by a live service call â€” there is no scripted path and no fallback data.

## 1. Bring the stack up

On the cluster:

```bash
cd ~/gsh-team07/Warehouse-optimization-solution
./deploy/runall.sh                # submits the job and waits for every service
./deploy/stack_status.sh          # node + per-service health, starts nothing
```

Five services must report healthy: cuOpt (25000), the slotting adapter (28002),
NeMo Guardrails (28003), the OpenShell governor (28004), and the WarehouseIQ UI
(28090). Both Nemotron models are hosted by NVIDIA, so the stack needs one GPU.
With `SELF_HOST_LIGHTNING=1` a sixth service, the Lightning NIM (28000), runs
locally and the job takes two GPUs.

From your laptop, with your own access key. Leave it running:

```bash
./deploy/forwardallports.sh <your-access-key>
```

Open <http://127.0.0.1:28090> at a browser width above 1400px so both side rails stay visible.

Before presenting, open the OpenShell console and use **Grant all 9 to
warehouse-planner** on the governed services card. Every gate is then cleared up
front and the run completes without stopping for approvals; leave it ungranted
if you want to show the governor holding a call instead.

Optionally verify the backend first:

```bash
python -m unittest discover -s tests
```

## 2. Frame the problem (Cockpit)

Use this section to tell the warehouse story before you show the agent. The
problem is simple to state but hard to solve: the warehouse has demand moving
faster than the slotting, so the right items are too deep in the building, some
stock is locked into the wrong zone, and the business loses time on every pick.

Before you frame the problem, give the audience the mental model of the cockpit:

- **New dataset** â€” clicking **New dataset** regenerates the whole warehouse from a
  seed. The mock service builds a new SKU master, warehouse layout, stock
  occupancy, inventory snapshot, and 14-day forecast. The seed can be fixed for a
  repeatable demo, or randomised for a fresh warehouse each time. The layout is
  still realistic: fast movers sit too far from the pick face, some slots are
  blocked, and chilled stock is kept in chilled zones.
- **Warehouse today** â€” this is the measured state of the building right now:
  picker travel, distance per pick, daily picks, forward-pick coverage, slot
  utilisation, blocked slots, and lines below reorder point. It is pure snapshot
  data, not a target or a forecast.
- **ABC class** â€” the class on each SKU is pre-assigned in the synthetic SKU
  master using a Pareto-style mix: **A** = fastest-moving lines that should live
  closest to the pick face, **B** = medium movers that belong in the mid-zone,
  **C** = slow movers that can sit deeper in reserve. In the demo data the split
  is intentionally skewed toward C lines, with a smaller but critical A segment.
- **Demand signal** â€” the left rail highlights the promoted line with the largest
  forecast uplift over the 14-day horizon, shows its forecast sparkline, and
  lists the top movers by forecast picks per day. The headline is chosen from the
  forecast data, not from a hand-written script.
- **Slotting vs demand** â€” the cockpit flags the gaps the plan can actually fix:
  fast movers far from the pick face, low forward-pick coverage, and slots that
  are available for relocation. It also marks what is *not* addressable by
  slotting, such as blocked slots, cold-chain locks, or lines below reorder point.
  Those items stay visible so the audience sees the boundary of the solution.
- **Golden zone** â€” the golden zone is the forward pick face, the closest and
  fastest-to-pick slots in the warehouse. In this demo it is zone A / zone 1, the
  area where class-A stock should be concentrated to reduce travel.
- **Forward pick coverage** â€” this is the share of class-A demand already stored
  in the forward pick zone. If class-A lines are sitting in reserve, coverage is
  low; if they are in the golden zone, coverage rises. The KPI tells you how well
  the warehouse is aligned to demand.

### How to narrate the cockpit

Land on **Cockpit** and connect the visuals to the story:

- **Demand signal** (left rail) â€” the promoted line with the largest forecast
  impact, its 14-day forecast sparkline with promotion days marked, and the top
  movers by forecast picks per day.
- **Warehouse map** (centre) â€” every real slot in the building, one rectangle
  each, coloured by ABC class, with the dispatch dock and the golden zone marked.
- **Warehouse today** (right rail) â€” picker travel, distance per pick, class-A
  coverage of the forward pick face, and forecast picks per day.

Suggested narration:

> This is a live distribution centre. Demand is pulling forward, but the layout
> is still pushing fast movers deep into reserve. The result is extra travel on
> every pick, lower forward-pick coverage, and slotting that no longer matches
> the shape of demand.

Point at **Slotting vs demand**. Note that some findings are flagged as not
addressable by slotting â€” lines below reorder point and slots blocked for
maintenance. The system is explicit about what it cannot fix.

## 3. Set the constraints (Move Plan)

Go to **Move Plan**. The left rail is the constraint contract handed to the solver:

- **Max moves per plan** â€” a hard cap cuOpt must respect.
- **Labour budget per window** â€” moves are packed into execution windows under this budget.
- **Lock cold-chain inventory** â€” no temperature-controlled SKU may be relocated.
- **Lock a specific SKU** â€” pin the headline SKU where it is.

These are not decoration. Changing one changes the problem sent to the solver.

## 4. Run the agents (DeepAgent Run)

Press **Run DeepAgent** and switch to **DeepAgent Run**. The span waterfall streams:

`ingest â†’ load_policies â†’ plan â†’ specialists â†’ optimize â†’ validate â†’ create_approval`

Call out, in order:

- **plan** â€” Nemotron, served locally through NVIDIA NIM, writes the objective and
  delegates. Its reasoning lines are shown verbatim.
- **specialists** â€” demand, inventory, and warehouse agents run with their own
  token and latency telemetry.
- **optimize** â€” a formal constrained assignment problem goes to **NVIDIA cuOpt**.
- **validate** â€” NeMo Guardrails checks the output stage.

The right rail shows the run objective, the constraints in force, real token counts,
and the cuOpt solve time and model size. The event log at the bottom is the raw trace.

Every privileged call in that trace was granted by the OpenShell governor. Nothing
ran unchecked.

## 5. Executive Dashboard

Open **Executive Dashboard** to show how the run is scored after DeepAgent finishes.
This view turns the run into a weighted scorecard so you can explain not just what the
agent did, but how well it performed.

### Overall agent score

The overall score is a weighted blend of the six category scores:

`Overall = 0.30 Ã— Business Impact + 0.20 Ã— Task Success + 0.15 Ã— Planning Quality + 0.15 Ã— Autonomy + 0.10 Ã— Efficiency + 0.10 Ã— Reliability`

In the UI, each category score is computed first, and then the weighted average produces
the final **Overall Agent Score** shown in the gauge.

### What the dashboard is showing

- **Overall Agent Score** â€” the final weighted score for the run.
- **Category score spread** â€” a quick visual comparison of the six category scores.
- **Category cards** â€” each card shows the category score and its sub-scores.
- **Summary panel** â€” the strongest category, the weakest category, and the run status.

### How category scores are calculated

Each category score is the **average of its sub-scores** on a 0â€“100 scale. That means
every sub-score inside a category contributes equally to that category.

For example:
- Business Impact = average of throughput improvement, cost reduction, resource utilization, cycle time reduction, SLA adherence, revenue uplift, inventory accuracy, and customer satisfaction.
- Task Success = average of task completion, goal achievement, first-time success, and execution accuracy.
- Planning Quality = average of planning score, replans required, decision optimality, constraint satisfaction, and root-cause identification.
- Autonomy = average of human intervention, autonomous completion, decision confidence, and escalation rate.
- Efficiency = average of latency, token consumption, compute cost, and response time.
- Reliability = average of failed executions, recovery rate, hallucination rate, exception handling, and missing-data resilience.

### How each sub-score is interpreted

- **Higher is better** for outcome metrics like throughput, SLA adherence, accuracy, and recovery.
- **Lower is better** for cost, latency, failed executions, intervention, and escalation.
- **Resource utilization** is best near the target labour fit, not simply at the highest value.
- Missing values are converted into estimated values from the run signals so the scorecard stays usable for every run.

`Category Score = average(all sub-scores in that category)`
 
#### Business Impact (30%)
- Throughput improvement â†’ from travel reduction / picks-hour uplift
- Cost reduction â†’ labour and travel savings mix
- Resource utilization â†’ how well labour budget is used
- Cycle time reduction â†’ per-pick travel reduction
- SLA adherence â†’ whether plan stayed within operating rules
- Revenue uplift â†’ demand flow preserved
- Inventory accuracy â†’ forward-pick / location quality
- Customer satisfaction â†’ service continuity indicator

Scoring logic:
- Percent-like metrics: higher is better, so raw % maps to score.
- Resource utilization: best near target utilization, not simply highest.
- Missing values fall back to estimated values from the run.
 
#### Task Success (20%)
- Task completion rate = did the run finish
- Goal achievement rate = produced a usable plan
- First-time success rate = completed without rework/replan
- Plan execution accuracy = constraint-compliant output
 
#### Planning Quality (15%)
- Planning score = overall plan quality
- Replans required = fewer is better
- Decision optimality = chosen plan vs best available plan
- Constraint satisfaction = zero-violation quality
- Root cause identification = how well the bottleneck was isolated
 
#### Autonomy (15%)
- Human intervention rate = lower is better
- Autonomous completion = finished without blocking
- Decision confidence = certainty of chosen action
- Escalation rate = lower is better
 
#### Efficiency (10%)
- Latency = lower is better
- Token consumption = lower is better
- Compute cost = blend of latency + tokens
- Response time = lower is better
- Resource utilization = labour fit against shift budget
 
#### Reliability (10%)
- Failed executions = lower is better
- Recovery rate = higher is better
- Hallucination rate = lower is better
- Exception handling = higher is better
- Missing-data resilience = higher is better

### Why the weights are set this way

| Category | Weight | Why it matters |
|---|---:|---|
| Business Impact | 30% | Primary business outcome; the agent exists to improve warehouse performance. |
| Task Success | 20% | The agent must actually complete the assigned task. |
| Planning Quality | 15% | Good planning is a core sign of agentic capability. |
| Autonomy | 15% | Measures how independently the system operates. |
| Efficiency | 10% | Important, but secondary to business value. |
| Reliability | 10% | Needed for trust and repeatable use in production. |

These are product weights for the demo. In a real deployment, they should be tuned
using site priorities and historical performance.

### How to explain it in the demo

Say that the dashboard answers four questions:
- Did the agent create business value?
- Did it complete the task?
- Did it plan well?
- Did it do it autonomously and reliably?

Then point to the category cards and explain that the final score is a weighted roll-up
of those six views, while each category score is the average of its own sub-scores.

## 6. Review the plan (Move Plan)

Back on **Move Plan** the solved state shows the travel reduction, the number of
moves, and the execution windows the moves were packed into under the labour budget.

The **Execution windows** cards explain how the move plan is staged across the shift:

- each window is one execution batch, here **4 windows** with **15 moves** each,
- the UI shows the estimated **travel saved per day** from that window,
- the **labour budget usage** shows how much of the available window capacity is consumed,
- the shift label (for example **Low-volume shift**) shows the operating context the solver used.

Use that segment to say: this is not just a static pick list; it is a time-phased plan
that fits the labour budget while preserving the expected travel reduction.

The **Optimisation objective** panel explains what the solver is trying to maximise:
travel saved and pick efficiency, while staying within the move cap, per-window labour
budget, slot capacity, temperature class, and locked SKU constraints.

The **What the optimiser reported** panel is the proof. It shows:

- the measured **travel reduction** and **replenishment reduction**,
- **constraint violations** should stay at **0**,
- the final **plan value** from the solver,
- the narrative summary that explains why the plan looks like this.

To validate the objective, say that the objective text must match the business goal,
the report must show zero violations, the plan must respect all constraints, and the
approved move list must be the same plan the solver reported.

The **plan value** is the solver's objective score for that move plan. It is the number
the optimiser uses to rank one plan against another under the same goal and constraints.
In this demo, a higher value means the plan is better because it delivers more net
benefit, such as travel reduction or replenishment improvement, while still staying
inside the labour budget, move cap, slot capacity, temperature class, and locked SKU
rules.

Compare plan value only across plans that use the same objective, weights, and
constraints. It is not an absolute business KPI. Use it to choose the better plan from
the solver's alternatives: higher value wins, lower value loses, and a zero or negative
value usually means the plan has little or no net gain under that formulation.

Expand any move in the manifest. You get:

- the plain-language reason cuOpt chose that slot,
- the labour minutes the move costs,
- the **alternatives considered** â€” the other candidate slots and the metre-picks
  each would have saved.

Approve or reject moves individually, or use **Approve all**.

## 7. Demonstrate governance (OpenShell)

Press **Send to WMS**. The write does not happen. A banner reports that the call is
**held by OpenShell** â€” `write_wms` is a per-call service, so every single write needs
its own approval.

Switch to **OpenShell**. The pending request is there. Approve it, return to the plan,
and the write completes on its own.

Suggested narration:

> The agent could not write to the warehouse system on its own authority. It asked,
> it waited, and a human approved that specific call. Every decision is in the audit log.

## 8. Show the outcome (Cockpit)

Return to **Cockpit**. The KPIs have moved against the recorded baseline â€” picker
travel and distance per pick fall, forward-pick coverage rises, and each card shows
the delta. The map now reflects the new slotting.

## 9. Show the failure path

Optionally, stop a service and re-run. The UI reports the failure and shows no plan.

> There is no fallback plan and no cached result. If the optimiser cannot run, you
> are told, rather than shown a number nobody can stand behind.

## 10. Close

- **Self-hosted NVIDIA stack** â€” Nemotron on NIM, cuOpt, and NeMo Guardrails all
  running on the cluster, not called as SaaS.
- **Governed autonomy** â€” OpenShell gates every privileged call, with per-call
  approval on writes.
- **Decisions, not dashboards** â€” a constrained, scheduled, WMS-ready move manifest
  with the alternatives it was chosen over.
- **No invented data** â€” every figure traces to a service response.

## Recommended timing

| Section | Minutes |
|---|---|
| Cockpit â€” the problem | 2 |
| Constraints | 1 |
| DeepAgent run | 3 |
| Executive Dashboard | 2 |
| Plan review | 2 |
| OpenShell approval | 2 |
| Outcome and close | 2 |
