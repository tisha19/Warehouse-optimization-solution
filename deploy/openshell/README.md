# NVIDIA OpenShell for the MAIW deep agent

OpenShell governs the deep agent on **two planes**, both declared in YAML in this
directory:

| Plane | Enforcer | Policy | What it governs |
|---|---|---|---|
| **Service egress** | OpenShell Governor (`services/openshell/governor.py`, port 8811) | `openshell-policies.yaml` | Every model (Nemotron NIM), cuOpt, MCP tool, Postgres, WCS and Azure-Blob call any agent makes |
| **Sandbox** | NVIDIA OpenShell gateway (Rust, gRPC) | `warehouse-sandbox.yaml` | The deep agent's own filesystem/shell (`execute`) tool inside a Docker sandbox |

No agent talks to a model, tool, cuOpt, MCP, database, filesystem, shell or the
internet directly: LLM + cuOpt HTTP traffic physically flows through the
governor's proxy, in-process chokepoints (the MCP tool registry, the psycopg2
connection factory) ask the governor for a grant before every call, and
shell/filesystem execution only exists inside the OpenShell sandbox (without a
sandbox the Deep Agents SDK exposes only its *virtual* in-state filesystem —
no host access).

## The OpenShell Governor (service egress + approvals + audit + admin UI)

```bash
python -m services.openshell.governor      # http://localhost:8811/
# or with docker compose (started automatically before the api):
docker compose up openshell-governor
```

- **Policy YAML** — `openshell-policies.yaml` declares the governed services
  (`llm.supervisor`, `llm.subagent`, `cuopt`, `postgres`, `tools.mcp`,
  `wcs.simulate`, `azure.blob`, `postgres.health`), their proxied upstreams and
  approval mode. `${VAR:-default}` values expand from the environment.
- **Admin console** at `http://localhost:8811/` — edit the policy YAML live
  (validated, backed up, hot-applied), watch pending requests pop up, approve or
  deny them, revoke grants, and tail the audit log of every registered call.
- **One-time per-user approval** — first use of an `approval: per_user` service
  by a user parks a pending request (the calling code long-polls up to
  `egress.approval.wait_seconds`); the admin approves once for that user; the
  grant persists under `state/` and all later calls auto-register in the audit
  log. `approval: auto` services register without an admin.
- **Proxy** — `/proxy/{service}/u/{user}/{upstream}/<path>` forwards to the
  upstream declared in the YAML. `ChatNVIDIA` keeps its class (so the Deep
  Agents **Nemotron 3 Ultra harness profile still auto-applies**); only its
  `base_url` points at the governor.
- **App-side env** — `OPENSHELL_GOVERNED` (`auto`|`true`|`false`),
  `OPENSHELL_GOVERNOR_URL` (default `http://localhost:8811`),
  `OPENSHELL_DEFAULT_USER`, `OPENSHELL_APPROVAL_WAIT_SECONDS`. Per-request
  identity comes from the `X-User-Id` header (set by the frontend / API gateway
  after JWT auth); scheduled runs act as `system-scheduler`.
- **Governor env** — `OPENSHELL_POLICY_FILE`, `OPENSHELL_STATE_DIR`,
  `OPENSHELL_GOVERNOR_HOST/PORT`.

# OpenShell sandbox for the MAIW deep agent (Phase 2)

The Deep Agents orchestrator can run its filesystem/shell operations inside a
**policy-governed NVIDIA OpenShell sandbox** instead of the host process. This keeps the
agent from executing generated code, scripts, or what-if simulations directly against the
host or live warehouse systems (WMS/TMS/YMS/robotics/dock schedules). It is **opt-in and
off by default** — the app runs unchanged until you both stand up a gateway and set
`OPENSHELL_ENABLED=true`.

## How it wires in

- `services/agents/openshell_backend.py` adapts an OpenShell sandbox session to the Deep
  Agents `BaseSandbox` backend protocol (`execute` / `upload_files` / `download_files`).
- `create_openshell_backend()` returns `None` — so `create_deep_agent` uses its normal
  in-process backend — unless **all** of these hold:
    1. `OPENSHELL_ENABLED=true`,
    2. the `openshell` Python package is importable, and
    3. an OpenShell gateway is reachable and the named sandbox is ready.
       Any missing prerequisite logs a warning and degrades to "no sandbox"; it never breaks a run.
- When active, `services/agents/deep_workflow.py` passes `backend=` to `create_deep_agent`
  and the `supervisor_start` event reports `"sandbox": "openshell"`.

## Prerequisites (not provided by this repo)

Standing up OpenShell requires infrastructure this repo does not vendor:

1. **Docker** running on the host (the gateway creates Docker-backed sandboxes).
2. The **`openshell` Python package** (`openshell==0.0.72`+). It is **not on the default
   PyPI index used by this project** — install it from NVIDIA's provided index/wheel.
3. A reachable **OpenShell gateway**. For a single dev machine, the container gateway:

    ```bash
    docker run -d --name openshell-gateway --restart unless-stopped \
      -p 127.0.0.1:8080:8080 \
      -v openshell-state:/var/openshell \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -e OPENSHELL_DRIVERS=docker \
      -e OPENSHELL_DB_URL=sqlite:/var/openshell/openshell.db \
      -e OPENSHELL_DISABLE_TLS=true \
      ghcr.io/nvidia/openshell/gateway@sha256:<pinned-digest>

    openshell gateway add http://127.0.0.1:8080 --local --name local
    openshell gateway select local
    openshell status   # must NOT say "No gateway configured"
    ```

4. The **sandbox** created with this repo's policy:

    ```bash
    openshell sandbox create --name warehouse-sandbox \
      --policy deploy/openshell/warehouse-sandbox.yaml --no-tty -- true
    # refresh an existing one:
    openshell policy set warehouse-sandbox \
      --policy deploy/openshell/warehouse-sandbox.yaml --wait
    ```

## Enable it

```dotenv
OPENSHELL_ENABLED=true
OPENSHELL_SANDBOX_NAME=warehouse-sandbox
OPENSHELL_DEFAULT_TIMEOUT_SECONDS=1800
```

Then restart the server and run a pipeline; the log prints
`OpenShell sandbox 'warehouse-sandbox' active as the Deep Agents backend.`

## Environment note (Windows dev)

On this workstation the Docker daemon was not running and `openshell` was not available on
the pip index at implementation time. The container-gateway path above works on Docker
Desktop with the WSL2 backend (the `/var/run/docker.sock` mount is exposed through WSL2).
Confirm both before setting `OPENSHELL_ENABLED=true`.

## Design note — what the agent executes in the sandbox

The warehouse specialists themselves are deterministic and call fixed MCP tools (Postgres,
cuOpt); they do **not** execute arbitrary code. The sandbox governs the deep agent's own
filesystem/shell tool use today, and is the safe execution layer for the planned **what-if
simulation / script-generation** capabilities (e.g. replaying dock congestion or labour
shortage scenarios before recommending actions). Adding those sandboxed tools is the
functional next step that gives OpenShell its full value.
