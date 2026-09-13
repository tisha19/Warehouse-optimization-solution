# WarehouseIQ Live Demo Guide

WarehouseIQ is a governed, multi-agent warehouse slotting copilot. Everything on screen
is produced by a live service call — there is no scripted path and no fallback data.

## 1. Bring the stack up

On the cluster:

```bash
cd ~/gsh-team07/Warehouse-optimization-solution
./deploy/runall.sh                # submits the job and waits for every service
./deploy/stack_status.sh          # node + per-service health, starts nothing
```

Five services must report healthy: cuOpt (25000), the slotting adapter (28002),
NeMo Guardrails (28003), the OpenShell governor (28004), and the WarehouseIQ UI
(28090). Both Nemotron models are hosted by NVIDIA, so nothing serves them
locally and the stack needs only one GPU.

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

- **New dataset** — clicking **New dataset** regenerates the whole warehouse from a
  seed. The mock service builds a new SKU master, warehouse layout, stock
  occupancy, inventory snapshot, and 14-day forecast. The seed can be fixed for a
  repeatable demo, or randomised for a fresh warehouse each time. The layout is
  still realistic: fast movers sit too far from the pick face, some slots are
  blocked, and chilled stock is kept in chilled zones.
- **Warehouse today** — this is the measured state of the building right now:
  picker travel, distance per pick, daily picks, forward-pick coverage, slot
  utilisation, blocked slots, and lines below reorder point. It is pure snapshot
  data, not a target or a forecast.
- **ABC class** — the class on each SKU is pre-assigned in the synthetic SKU
  master using a Pareto-style mix: **A** = fastest-moving lines that should live
  closest to the pick face, **B** = medium movers that belong in the mid-zone,
  **C** = slow movers that can sit deeper in reserve. In the demo data the split
  is intentionally skewed toward C lines, with a smaller but critical A segment.
- **Demand signal** — the left rail highlights the promoted line with the largest
  forecast uplift over the 14-day horizon, shows its forecast sparkline, and
  lists the top movers by forecast picks per day. The headline is chosen from the
  forecast data, not from a hand-written script.
- **Slotting vs demand** — the cockpit flags the gaps the plan can actually fix:
  fast movers far from the pick face, low forward-pick coverage, and slots that
  are available for relocation. It also marks what is *not* addressable by
  slotting, such as blocked slots, cold-chain locks, or lines below reorder point.
  Those items stay visible so the audience sees the boundary of the solution.
- **Golden zone** — the golden zone is the forward pick face, the closest and
  fastest-to-pick slots in the warehouse. In this demo it is zone A / zone 1, the
  area where class-A stock should be concentrated to reduce travel.
- **Forward pick coverage** — this is the share of class-A demand already stored
  in the forward pick zone. If class-A lines are sitting in reserve, coverage is
  low; if they are in the golden zone, coverage rises. The KPI tells you how well
  the warehouse is aligned to demand.

### How to narrate the cockpit

Land on **Cockpit** and connect the visuals to the story:

- **Demand signal** (left rail) — the promoted line with the largest forecast
  impact, its 14-day forecast sparkline with promotion days marked, and the top
  movers by forecast picks per day.
- **Warehouse map** (centre) — every real slot in the building, one rectangle
  each, coloured by ABC class, with the dispatch dock and the golden zone marked.
- **Warehouse today** (right rail) — picker travel, distance per pick, class-A
  coverage of the forward pick face, and forecast picks per day.

Suggested narration:

> This is a live distribution centre. Demand is pulling forward, but the layout
> is still pushing fast movers deep into reserve. The result is extra travel on
> every pick, lower forward-pick coverage, and slotting that no longer matches
> the shape of demand.

Point at **Slotting vs demand**. Note that some findings are flagged as not
addressable by slotting — lines below reorder point and slots blocked for
maintenance. The system is explicit about what it cannot fix.

## 3. Set the constraints (Move Plan)

Go to **Move Plan**. The left rail is the constraint contract handed to the solver:

- **Max moves per plan** — a hard cap cuOpt must respect.
- **Labour budget per window** — moves are packed into execution windows under this budget.
- **Lock cold-chain inventory** — no temperature-controlled SKU may be relocated.
- **Lock a specific SKU** — pin the headline SKU where it is.

These are not decoration. Changing one changes the problem sent to the solver.

## 4. Run the agents (DeepAgent Run)

Press **Run DeepAgent** and switch to **DeepAgent Run**. The span waterfall streams:

`ingest → load_policies → plan → specialists → optimize → validate → create_approval`

Call out, in order:

- **plan** — Nemotron, served locally through NVIDIA NIM, writes the objective and
  delegates. Its reasoning lines are shown verbatim.
- **specialists** — demand, inventory, and warehouse agents run with their own
  token and latency telemetry.
- **optimize** — a formal constrained assignment problem goes to **NVIDIA cuOpt**.
- **validate** — NeMo Guardrails checks the output stage.

The right rail shows the run objective, the constraints in force, real token counts,
and the cuOpt solve time and model size. The event log at the bottom is the raw trace.

Every privileged call in that trace was granted by the OpenShell governor. Nothing
ran unchecked.

## 5. Review the plan (Move Plan)

Back on **Move Plan** the solved state shows the travel reduction, the number of
moves, and the execution windows the moves were packed into under the labour budget.

Expand any move in the manifest. You get:

- the plain-language reason cuOpt chose that slot,
- the labour minutes the move costs,
- the **alternatives considered** — the other candidate slots and the metre-picks
  each would have saved.

Approve or reject moves individually, or use **Approve all**.

## 6. Demonstrate governance (OpenShell)

Press **Send to WMS**. The write does not happen. A banner reports that the call is
**held by OpenShell** — `write_wms` is a per-call service, so every single write needs
its own approval.

Switch to **OpenShell**. The pending request is there. Approve it, return to the plan,
and the write completes on its own.

Suggested narration:

> The agent could not write to the warehouse system on its own authority. It asked,
> it waited, and a human approved that specific call. Every decision is in the audit log.

## 7. Show the outcome (Cockpit)

Return to **Cockpit**. The KPIs have moved against the recorded baseline — picker
travel and distance per pick fall, forward-pick coverage rises, and each card shows
the delta. The map now reflects the new slotting.

## 8. Show the failure path

Optionally, stop a service and re-run. The UI reports the failure and shows no plan.

> There is no fallback plan and no cached result. If the optimiser cannot run, you
> are told, rather than shown a number nobody can stand behind.

## 9. Close

- **Self-hosted NVIDIA stack** — Nemotron on NIM, cuOpt, and NeMo Guardrails all
  running on the cluster, not called as SaaS.
- **Governed autonomy** — OpenShell gates every privileged call, with per-call
  approval on writes.
- **Decisions, not dashboards** — a constrained, scheduled, WMS-ready move manifest
  with the alternatives it was chosen over.
- **No invented data** — every figure traces to a service response.

## Recommended timing

| Section | Minutes |
|---|---|
| Cockpit — the problem | 2 |
| Constraints | 1 |
| DeepAgent run | 3 |
| Plan review | 2 |
| OpenShell approval | 2 |
| Outcome and close | 2 |
