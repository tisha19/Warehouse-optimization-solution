# WarehouseIQ Live Demo Guide

WarehouseIQ is a governed, multi-agent warehouse slotting copilot. Everything on screen
is produced by a live service call — there is no scripted path and no fallback data.

## 1. Bring the stack up

On the cluster:

```bash
cd ~/gsh-team07/Warehouse-optimization-solution
sbatch deploy/slurm_stack.sbatch
./deploy/stack_status.sh          # node + per-service health
```

Six services must report healthy: cuOpt (5000), the slotting adapter (8002),
NeMo Guardrails (8003), the OpenShell governor (8004), the Nemotron NIM (8000),
and the WarehouseIQ UI (8090).

From your laptop:

```bash
ssh -N -L 8090:<STACK_NODE>:8090 ssh.axisapps.io -l <your-access-key>
```

Open <http://127.0.0.1:8090> at a browser width above 1400px so both side rails stay visible.

Optionally verify the backend first:

```bash
python -m unittest discover -s tests
```

## 2. Frame the problem (Cockpit)

Land on **Cockpit**. Three things are on screen, all live:

- **Demand signal** (left rail) — the promoted line with the largest forecast impact,
  its 14-day forecast sparkline with promotion days marked, and the top movers by
  forecast picks per day.
- **Warehouse map** (centre) — every real slot in the building, one rectangle each,
  coloured by ABC class, with the dispatch dock and the golden zone marked.
- **Warehouse today** (right rail) — picker travel, distance per pick, class-A
  coverage of the forward pick face, and forecast picks per day.

Suggested narration:

> This is a live distribution centre. The highest-demand line is on promotion and
> sitting tens of metres from the pick face. Class-A coverage of the forward pick
> zone is under ten percent — nearly every fast pick is a long walk into reserve.

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
