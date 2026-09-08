# Self-hosted stack on Curiosity v2

Runs the warehouse planner with NVIDIA cuOpt, NeMo Guardrails, OpenShell and two
Nemotron NIMs self-hosted on one DGX-B300 node.

## Why a batch job, not `srun`

An interactive `srun --pty` session dies with your SSH connection and takes every
container with it (we lost job 5221 that way). The stack therefore runs under
**`sbatch`**, which keeps the allocation and the containers alive after you log out.

## Quick start

```bash
cd ~/gsh-team07/Warehouse-optimization-solution
sbatch deploy/slurm_stack.sbatch     # start everything
./deploy/stack_status.sh             # where it runs + per-service health
./deploy/stack_down.sh               # stop and release the GPUs
```

`stack_status.sh` is safe to run from the login node and prints the node the stack
landed on, taken from `deploy/state/stack.env`.

## Restarting / resuming

| Situation | What to do |
|---|---|
| Job still `RUNNING`, one service died | `./deploy/stack_status.sh` to confirm, then on the node: `./deploy/cluster_up.sh` (idempotent, replaces containers) |
| Job ended or was cancelled | `sbatch deploy/slurm_stack.sbatch` |
| You lost your SSH session | Nothing to do — the batch job keeps running. Reconnect and run `./deploy/stack_status.sh` |
| Time limit approaching | `sbatch` a new job before the old one expires, then `./deploy/stack_down.sh` for the old one |
| Wrong node / GPUs busy | `./deploy/stack_down.sh`, then resubmit; Slurm picks any idle node |

Attach to the running job's node for hands-on work:

```bash
srun --jobid=$(squeue -u $USER -h -n warehouse-stack -o %i) --pty bash -l
module load rootless-docker/1.75
docker ps
docker logs -f warehouse-nim-supervisor
```

## Ports

| Service | Port | Health check |
|---|---|---|
| cuOpt solver | 5000 | `/cuopt/health` |
| cuOpt slotting adapter | 8002 | `/health` |
| NeMo Guardrails | 8003 | `/v1/health` |
| OpenShell Governor | 8004 | `/api/v1/healthz` |
| Nemotron 3 Ultra (supervisor) | 8000 | `/v1/health/ready` |
| Nemotron 3.5 Lightning (sub-agent) | 8010 | `/v1/health/ready` |
| Planner UI | 8090 | `/api/state` |

## Viewing the UI from your laptop

Compute nodes are not directly reachable, so tunnel through the login node:

```bash
ssh -N -L 8090:<STACK_NODE>:8090 ssh.axisapps.io -l <your-access-key>
```

Then open <http://127.0.0.1:8090>. Get `<STACK_NODE>` from `./deploy/stack_status.sh`.

## GPU budget

The Slurm quota is **4 GPUs**, and Ultra's smallest profile (`vllm-nvfp4-tp4-pp1-84.0`)
needs exactly 4. Both NIMs therefore share the same GPUs and neither may take vLLM's
default ~90% of each card:

```bash
ULTRA_GPU_UTIL=0.55       # ~151 GB of 275 GB per B300
LIGHTNING_GPU_UTIL=0.12   # ~33 GB on GPU 3
```

Ultra starts first because tensor parallelism needs symmetric free memory on all
four cards. Raise `ULTRA_GPU_UTIL` for a longer KV cache if you drop Lightning.

## What is self-hosted vs. not

Self-hosted on the node: cuOpt, the slotting adapter, NeMo Guardrails, the OpenShell
Governor, and both Nemotron NIMs.

Not self-hosted: **WMS, ERP and forecast data are synthetic** (`mocks/enterprise_services.py`)
until `WMS_URL` / `ERP_URL` / `FORECAST_URL` point at real systems. The NVIDIA cloud NIM
stays configured as an automatic fallback (`NIM_CLOUD_FALLBACK=true`) so the planner keeps
working if a local NIM is down; set it to `false` to make that a hard failure.

## Why cuOpt needs an adapter

The planner posts to `CUOPT_URL/solve/slotting`, which cuOpt does not expose.
`deploy/cuopt_adapter/` builds a linear assignment model where the cost of putting
SKU *i* in slot *j* is `daily_picks(i) x distance_to_pick_face(j)`, submits it to
cuOpt (`POST /cuopt/request`, then polls `/cuopt/solution/{reqId}` — the API is
asynchronous), and maps the solution back to planner moves. If cuOpt is unreachable
it returns 503 rather than inventing a plan.

## OpenShell

`services/openshell/` runs the Governor; `deploy/openshell/openshell-policies.yaml`
declares the policy. Approvals are one-time per user except `write_wms`, which is
`per_call` and needs a fresh admin approval every time.

```bash
curl -s http://<STACK_NODE>:8004/api/v1/requests      # pending approvals
curl -s -X POST http://<STACK_NODE>:8004/api/v1/requests/<id>/approve \
     -H 'Content-Type: application/json' -d '{"resolved_by":"admin"}'
```

The app keeps a local allowlist as a hard floor, so an unreachable Governor can never
silently widen permissions.

## Troubleshooting

**Stale rootless-docker lock** after an unclean job exit:

```bash
rm -rf /raid/docker/tmp/xdg_runtime_dir_$(id -u)/dockerd-rootless
start_rootless_docker
```

**Stale shell exports shadow `.env`** — `services/config.py` lets the file win by
default; set `WAREHOUSE_ENV_PRECEDENCE=process` if an orchestrator injects config.

**Model cache** lives at `/raid/docker/tmp/nim-cache-$USER` (node-local NVMe, ~25 TB
free). It is wiped with the node, so a job on a new node re-downloads weights.
