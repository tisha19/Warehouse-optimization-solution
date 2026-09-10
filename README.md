# AI-Driven Warehouse Slotting & Inventory Optimization Platform

Production-oriented integration scaffold for a warehouse DeepAgent workflow. Real NVIDIA and cuOpt services are required; only WMS, ERP, and forecasting endpoints have local synthetic mocks.

## WarehouseIQ Operator UI

WarehouseIQ is a four-screen operator console: a **Cockpit** with the live demand signal and a slot-level warehouse map, a **Move Plan** with the constraint rail and the WMS-ready move manifest, a **DeepAgent Run** span waterfall, and the **OpenShell** governance console.

The UI is a React + Vite application in `web/`. It must be built before the server will start:

```bash
cd web && npm ci && npm run build
cd .. && source .venv/bin/activate
python showcase_server.py --port 8090
```

Open `http://127.0.0.1:8090`.

Every number on screen comes from a live service call. WMS, ERP, and forecasting are backed by the synthetic generator in `mocks/`; NVIDIA NIM, cuOpt, NeMo Guardrails, and OpenShell are the real services. **Nothing is faked and there is no fallback path** — if a service is unreachable, the screen shows the failure instead of substituting a plan.

## Production Architecture

```text
WMS / ERP / Forecast APIs
    -> LangGraph DeepAgent
    -> Nemotron through NVIDIA NIM
    -> specialist agents
    -> NVIDIA cuOpt constrained solver
    -> NeMo Guardrails + OpenShell policy
    -> regression and constraint harness
    -> human approval
    -> approved WMS write-back
    -> agent evaluation and tracing
```

## Production Modules

- `agents/workflow.py`: LangGraph orchestration and state flow
- `agents/specialists.py`: Nemotron-backed specialist-agent runner
- `services/config.py`: Environment-driven production configuration
- `services/http_client.py`: Shared authenticated JSON HTTP transport
- `services/enterprise.py`: WMS, ERP, and forecasting adapters
- `services/nvidia.py`: NIM, cuOpt, and Guardrails clients
- `tools/evaluation.py`: Agent quality, latency, errors, and trace recording
- `tools/evaluation_suite.py`: Evaluation entry point for all agents
- `tools/governance.py`: OpenShell-style permissions and human approvals
- `tools/harness.py`: Hard constraints and regression cases
- `production_main.py`: Planning and approval-gated write-back CLI
- `showcase_server.py`: JSON API for the UI; serves the built bundle from `web/dist`
- `showcase/controller.py`: Warehouse layout, demand signal, run, and commit endpoints
- `showcase/demand.py`: Forecast-derived pick rates and promotion signal
- `web/`: React + Vite operator UI (Cockpit, Move Plan, DeepAgent Run, OpenShell)
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
