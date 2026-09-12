# AI-Driven Warehouse Slotting & Inventory Optimization Platform

Production-oriented integration scaffold for a warehouse DeepAgent workflow. Real NVIDIA and cuOpt services are required; only WMS, ERP, and forecasting endpoints have local synthetic mocks.

## WarehouseIQ Operator UI

WarehouseIQ is a four-screen operator console: a **Cockpit** with the live demand signal, a slot-level warehouse map and Nemotron's reading of the slotting gaps, a **Move Plan** with the constraint rail, the WMS-ready move manifest and the audit of everything already written, a **DeepAgent Run** showing the orchestrator, its specialists and each cuOpt solve round as they happen, and the **OpenShell** governance console.

### Running it on the cluster

This is how the stack is actually run. On the login node:

```bash
cd ~/gsh-team07/Warehouse-optimization-solution && ./deploy/runall.sh
```

It submits the Slurm job, waits for every service to answer its health check, and
prints where it landed. Re-running it leaves a healthy stack alone.

Then on your own machine, with your own access key. Leave it running; Ctrl-C
closes the tunnels:

```bash
./deploy/forwardallports.sh <your-access-key>
```

| Service | Local URL | Port |
| --- | --- | --- |
| **WarehouseIQ UI** | **http://127.0.0.1:28090** | **28090** |
| cuOpt solver | http://127.0.0.1:25000 | 25000 |
| cuOpt slotting adapter | http://127.0.0.1:28002 | 28002 |
| NeMo Guardrails | http://127.0.0.1:28003 | 28003 |
| OpenShell governor | http://127.0.0.1:28004 | 28004 |

The ports sit in the 28xxx range because the compute node is shared and the
usual defaults collide with other teams. `./deploy/stack_status.sh` reports
health without starting anything, and `./deploy/stack_down.sh` releases the GPU.

### Running it locally

The UI is a React + Vite application in `web/`, and the server will not start
without the built bundle:

```bash
cd web && npm ci && npm run build
cd .. && source .venv/bin/activate
python showcase_server.py --port 28090
```

Every number on screen comes from a live service call. WMS, ERP, and forecasting are backed by the synthetic generator in `mocks/`; NVIDIA NIM, cuOpt, NeMo Guardrails, and OpenShell are the real services. **Nothing is faked and there is no fallback path** — if a service is unreachable, the screen shows the failure instead of substituting a plan.

Every model call and every read of an enterprise system is announced to the
OpenShell governor first, including the cockpit's own assessment: revoking the
grants in the console stops them.

## Production Architecture

```text
WMS / ERP / Forecast APIs
    -> deepagents orchestrator on Nemotron 3 Ultra
    -> demand / inventory / warehouse specialists on Nemotron 3.5 Lightning
    -> NVIDIA cuOpt constrained solver, re-solved until nothing is left to gain
    -> NeMo Guardrails on the input and the output
    -> OpenShell governs every model call and every enterprise read
    -> regression and constraint harness
    -> human approval
    -> approved WMS write-back
    -> agent evaluation and tracing
```

Both models are hosted by NVIDIA at `https://inference-api.nvidia.com/v1`; the
stack itself needs one GPU, for cuOpt. The orchestrator runs under the
deepagents Nemotron harness (13 middleware), which repairs text-shaped tool
calls and strips stray reasoning tags.

## Production Modules

- `agents/deep_workflow.py`: the deepagents orchestrator, its tools and the solve loop
- `agents/digest.py`: the aggregated warehouse summary handed to the agents
- `services/config.py`: Environment-driven production configuration
- `services/harness_profile.py`: binds the hosted Nemotron models to the deepagents harness
- `services/http_client.py`: Shared authenticated JSON HTTP transport
- `services/enterprise.py`: WMS, ERP, and forecasting adapters
- `services/nvidia.py`: NIM, cuOpt, and Guardrails clients
- `services/openshell/governor.py`: the egress governor: policy, grants, approvals, audit
- `tools/evaluation.py`: Agent quality, latency, errors, and trace recording
- `tools/evaluation_suite.py`: Evaluation entry point for all agents
- `tools/governance.py`: OpenShell-style permissions and human approvals
- `tools/harness.py`: Hard constraints and regression cases
- `production_main.py`: Planning and approval-gated write-back CLI
- `showcase_server.py`: JSON API for the UI; serves the built bundle from `web/dist`
- `showcase/controller.py`: Warehouse layout, demand signal, run, and commit endpoints
- `showcase/analysis.py`: Nemotron's reading of the measured slotting gaps
- `showcase/kpis.py`: the measured KPIs and the slotting headroom arithmetic
- `showcase/demand.py`: Forecast-derived pick rates and promotion signal
- `web/`: React + Vite operator UI (Cockpit, Move Plan, DeepAgent Run, OpenShell)
- `deploy/`: cluster bring-up, port forwarding, and per-service diagnostics
- `mocks/enterprise_services.py`: Synthetic WMS, ERP, and forecast services only
- `tests/test_production.py`: Offline governance and regression tests
- `.env.example`: Endpoint and credential configuration template
- `requirements-production.txt`: Production Python dependencies

### Production Workflow

```text
WMS / ERP / Forecast APIs
    -> LangGraph DeepAgent
    -> Nemotron through NIM
    -> specialist agents
    -> cuOpt constrained solver
    -> NeMo Guardrails + OpenShell policy
    -> regression and constraint harness
    -> pending human approval
    -> approved WMS write-back
    -> agent evaluation traces and KPI monitoring
```

Configure service URLs from `.env.example`, install `requirements-production.txt`, and run `python production_main.py --actor planner-service` or call `ProductionWarehouseWorkflow.run()`. The default demand, inventory, and warehouse specialist path invokes Nemotron through NIM; deterministic runners can be injected for evaluation and controlled tests. The workflow fails when cuOpt or required production services are absent; it does not fall back to the heuristic optimizer. WMS write-back requires an `APPROVED` approval record and a policy-authorized actor.

Run the offline governance checks with:

```bash
python -m unittest discover -s tests -v
```

The integration modules use standard-library HTTP clients so the service boundaries can be tested without exposing credentials or requiring a network connection. Actual NIM, cuOpt, Guardrails, OpenShell, and WMS services must be deployed and configured separately.

### Synthetic Enterprise Service Mock

`mocks/enterprise_services.py` provides a local HTTP gateway for development and demonstrations. It generates deterministic synthetic WMS, ERP, inventory, forecast, and inbound data only. NVIDIA NIM, Guardrails, OpenShell, and cuOpt are never mocked; configure their real service URLs and credentials in the environment.

Start it in one terminal:

```bash
python -m mocks.enterprise_services --port 9100 --seed 7
```

In another terminal, point the service URLs at the gateway:

```bash
export WMS_URL=http://127.0.0.1:9100
export ERP_URL=http://127.0.0.1:9100
export FORECAST_URL=http://127.0.0.1:9100
export APPROVAL_STORE=results/approvals.json
# Set NIM_BASE_URL, CUOPT_URL, NEMO_GUARDRAILS_URL, and
# OPENSHELL_URL to your actual NVIDIA and cuOpt service endpoints.
python production_main.py --actor planner-service
```

The mock server is for local integration testing only. It does not represent WMS or ERP production behavior or security, and it contains no NVIDIA or cuOpt implementation.

---

**Version**: 1.0.0 | **Status**: Production-Ready
