# GPU cluster deployment runbook

Self-hosts the NVIDIA stack for the warehouse planner and wires the application to it.
Scripts are Linux/bash and assume Docker with the NVIDIA Container Toolkit.

## What each component does

| Component | Purpose | If it is down |
|---|---|---|
| Supervisor NIM (Nemotron 3 Ultra) | Produces the structured plan | Falls back to **NVIDIA cloud NIM** |
| Sub-agent NIM (compact Nemotron) | Demand / inventory / warehouse specialists | Falls back to **NVIDIA cloud NIM** |
| cuOpt + slotting adapter | Solves the constrained slotting assignment | Falls back to the deterministic local planner (clearly labelled in the UI) |
| NeMo Guardrails | Validates input, output, and tool calls | Falls back to the local baseline policy |
| OpenShell | Authorises agent tool use | Falls back to the local tool allowlist |
| Enterprise gateway | WMS / ERP / forecast data | Falls back to synthetic warehouse data |

The planner never blocks on a missing service, and it never silently pretends a
missing service succeeded. Run `python -m tools.wiring_report` at any time to see
the live picture.

## Cloud fallback

`NIMClient` calls `NIM_BASE_URL` first. On any connection or HTTP failure it retries
against `NIM_CLOUD_BASE_URL` using `NIM_CLOUD_API_KEY`, then records which endpoint
served the request in `NIMClient.active_endpoint`.

```bash
NIM_CLOUD_FALLBACK=true
NIM_CLOUD_BASE_URL=https://integrate.api.nvidia.com/v1
NIM_CLOUD_MODEL=nvidia/nemotron-3-ultra-550b-a55b
NIM_CLOUD_API_KEY=nvapi-...
```

Set `NIM_CLOUD_FALLBACK=false` to make a self-hosted outage a hard failure.

## Run order

```bash
cd deploy
vim config.env          # NGC key, image tags, GPU indices, cloud API key

./00_preflight.sh           # driver, docker, GPU passthrough, disk, ports
./01_ngc_login.sh           # registry auth + image pulls
./02_nim_supervisor.sh      # Nemotron 3 Ultra NIM      -> :8000
./03_nim_subagent.sh        # compact Nemotron NIM      -> :8010
./04_cuopt.sh               # cuOpt :5000 + adapter     -> :8002
./05_guardrails.sh          # NeMo Guardrails           -> :8003
./06_openshell.sh           # OpenShell gateway         -> :8004
./07_enterprise_gateway.sh  # WMS / ERP / forecast      -> :9100
./08_write_env.sh           # generates ../.env
./09_healthcheck.sh         # wiring report + harness
./10_run_app.sh             # planner UI                -> :8080
```

## Before you start: verify image tags

`config.env` ships with placeholder tags. Container repositories and tags differ per
release and per entitlement, so confirm each one in the
[NGC catalog](https://catalog.ngc.nvidia.com/) and update `config.env`. `01_ngc_login.sh`
pulls every image up front so a wrong tag fails immediately rather than mid-deployment.

## GPU placement

Nemotron 3 Ultra is a large mixture-of-experts model and needs several GPUs. Check the
model card for the required GPU count and memory, then set the device lists:

```bash
nvidia-smi -L                  # list GPUs
export SUPERVISOR_GPUS=0,1,2,3,4,5,6,7
export SUBAGENT_GPUS=8
export CUOPT_GPUS=9
```

## Why cuOpt needs an adapter

The planner posts a warehouse slotting problem to `CUOPT_URL/solve/slotting`. cuOpt
does not expose that contract, so `cuopt_adapter/` bridges the two. It builds a linear
sum assignment model where the cost of putting SKU *i* in slot *j* is

```
cost(i, j) = daily_picks(i) x distance_to_pick_face(j)
```

Minimising the total yields the layout with the least picker walking. Locked SKUs and
cold-chain items are excluded before the model is built, and the move cap is applied to
the solution. If cuOpt is unreachable or returns no primal solution the adapter answers
`503` instead of inventing a plan.

`CUOPT_SOLVE_PATH` defaults to `/cuopt/request`. Confirm the request path for your cuOpt
version and override it in `config.env` if it differs.

## The harness

`tools/harness.py` holds the hard constraints and regression cases; `tools/evaluation.py`
scores agent output. Both run on CPU as part of `09_healthcheck.sh` and gate the plan
before any approval is created. They are not a GPU service.

## Data that is real vs. generated

Wired to real infrastructure once these scripts run: Nemotron inference, cuOpt
optimisation, guardrail validation, OpenShell authorisation, approvals, and the
evaluation harness.

Still generated: **WMS, ERP, and forecast records**, unless you repoint `WMS_URL`,
`ERP_URL`, and `FORECAST_URL` at real systems. Those three adapters speak plain JSON
over HTTP (`services/enterprise.py`), so pointing them at production endpoints that
match the same request shapes is the only change required.

## Troubleshooting

**Stale environment variables shadow `.env`.** This is the most common wiring problem.
The loader in `services/config.py` now lets `.env` win by default; set
`WAREHOUSE_ENV_PRECEDENCE=process` if an orchestrator should inject config instead.

```bash
unset NIM_BASE_URL NIM_MODEL NIM_API_KEY NEMO_GUARDRAILS_URL CUOPT_URL RAIL_API_URL LLM_NIM_URL
```

**Model id mismatch.** `NIM_MODEL` must match what the NIM advertises:

```bash
curl -s http://127.0.0.1:8000/v1/models
```

**Inspect a service.**

```bash
docker logs -f warehouse-nim-supervisor
docker ps --filter name=warehouse-
```

**Tear down.**

```bash
docker rm -f $(docker ps -aq --filter name=warehouse-)
```
