# Self-hosted stack on Curiosity v2

Runs WarehouseIQ with NVIDIA cuOpt, NeMo Guardrails, the OpenShell governor and
self-hosted Nemotron NIMs on DGX-B300 nodes.

## Start everything

```bash
cd ~/gsh-team07/Warehouse-optimization-solution
./deploy/runall.sh
```

`runall.sh` is the only command you normally need. It prints what is already up,
submits whatever is missing, follows the boot logs until each service answers its
health check, prints the final status, and ends with the URL and the exact
port-forward command.

## View the UI from your laptop

The application listens on port 8090 of whichever node the stack landed on, so
the tunnel needs that node name. `runall.sh` prints the ready-made command; to
build it yourself:

```bash
# 1. find the node
ssh ssh.axisapps.io -l <your-access-key> \
  'sed -n "s/^STACK_NODE=//p" gsh-team07/Warehouse-optimization-solution/deploy/state/stack.env'

# 2. forward the port (leave this running)
ssh -N -L 8090:<STACK_NODE>:8090 ssh.axisapps.io -l <your-access-key>
```

Then open <http://127.0.0.1:8090>.

If the tunnel drops, re-run step 2 — the node only changes when the Slurm job is
resubmitted. On Windows the same commands work in PowerShell.

## Uptime

Both batch jobs request the full 30 days the `gsh-team07` QoS allows and set
`--requeue`. This has to be right at submission: **a running job's time limit
cannot be raised by the owning user** (`scontrol update` returns
`Access/permission denied`). Being batch jobs, they are unaffected by your SSH
session closing.

## Scripts

| Script | Run from | What it does |
|---|---|---|
| `runall.sh` | login node | Status → start what is missing → follow logs → final status → URL. The normal entry point. |
| `slurm_stack.sbatch` | `sbatch` | The application stack: cuOpt, the cuOpt adapter, NeMo Guardrails, the OpenShell governor and the WarehouseIQ UI/API. Holds the allocation and re-runs `cluster_up.sh` if containers vanish. |
| `ultra_serve.sbatch` | `sbatch` | Serves Nemotron 3 Ultra 550B-A55B at TP=4 and holds the allocation. Rebuilds the container if it disappears. |
| `cluster_up.sh` | compute node | Brings the stack up idempotently: starts rootless docker, pulls images, starts containers, builds the web bundle, applies the Guardrails config. Called by `slurm_stack.sbatch`; safe to re-run. |
| `stack_status.sh` | login node | Per-service health for the application stack, plus the Slurm queue. Read-only. |
| `stack_verify.sh` | login node | End-to-end check: drives a run through the governor and the approval path. |
| `stack_down.sh` | login node | Cancels the stack job and releases the GPUs. |
| `redeploy_adapter.sh` | `srun --overlap` | Rebuilds and restarts the cuOpt adapter container after a code change, discovering `DOCKER_HOST` from the running rootless daemon. |
| `ultra_status.sh` | login node | Ultra job lifetime, node and endpoint health. Takes a job id. |
| `ultra_follow.sh` | `srun --overlap` | Follows the Ultra container log live with a GPU-memory line every minute. |
| `ultra_stream_test.py` | compute node | Streams a completion from Ultra so the reasoning trace and answer can be watched live. |
| `ultra_toolcall_test.py` | compute node | Confirms Ultra returns OpenAI-style `tool_calls`, which Deep Agents requires. |
| `ultra_tp4_test.sbatch` / `ultra_tp4_test.sh` | `sbatch` | The capacity experiment: frees every GPU, runs Ultra alone at TP=4 and reports readiness, per-GPU memory and a timed completion. Kept so the evidence behind the GPU request can be reproduced. |
| `ultra_tp4_result.sh` | anywhere | Extracts the verdict from a `ultra-tp4-test-*.log`, minus the loader noise. |
| `fix_line_endings.py` | anywhere | Strips CRLF from files copied in from Windows. Run it after any `scp` from a Windows machine — a stray `\r` makes bash fail in ways that are hard to read. |
| `guardrails_warehouse_config.json` | — | The NeMo Guardrails rails config. `cluster_up.sh` rewrites its model id to whatever the NIM actually serves. |
| `openshell/` | — | Governor policy (`openshell-policies.yaml`) and sandbox definitions. |

## Restarting and resuming

| Situation | What to do |
|---|---|
| Anything is down | `./deploy/runall.sh` |
| Job still `RUNNING`, one container died | The stack job re-runs `cluster_up.sh` itself within a minute. To force it: `srun --jobid=<ID> --overlap ./deploy/cluster_up.sh` |
| Adapter code changed | `srun --jobid=<ID> --overlap bash deploy/redeploy_adapter.sh` |
| UI code changed | Rebuild the bundle, then restart the server: `srun --jobid=<ID> --overlap pkill -f 'showcase_server\.py'` (the monitor loop restarts it within ~60s) |
| You lost your SSH session | Nothing to do — the batch jobs keep running. Reconnect and run `./deploy/stack_status.sh` |

## GPU allocation

The `gsh-team07` QoS is capped at `cpu=128, gres/gpu=4`. The full topology needs
six GPUs — Ultra at TP=4, the specialist NIM on one, cuOpt on one — so Ultra and
the application stack cannot both hold GPUs until the quota is raised.

The quota is enforced across Slurm **and** Kubernetes combined and blocks
submission rather than scheduling, so a successor job cannot even be queued:

```
Double-dipping blocked by Slurm! Requesting 4 GPU(s), but you currently have
0 GPU(s) in K8s and 4 GPU(s) in Slurm. Combined (8) exceeds your allowed quota of 4
```

Once the allocation increases, request more without editing any file:

```bash
sbatch --gres=gpu:N deploy/slurm_stack.sbatch
```

## Environment notes

Several things are not on the default non-interactive `PATH` and bite silently:

- **Slurm**: `export PATH=/cm/local/apps/slurm/current/bin:$PATH` and
  `export SLURM_CONF=/cm/shared/apps/slurm/etc/slurm/slurm.conf`.
- **Rootless docker**: lives in `/cm/shared/apps/rootless-docker/bin` and answers
  on a per-user socket, `unix:///raid/docker/tmp/xdg_runtime_dir_$(id -u)/docker.sock`.
  The daemon does not survive the job that started it, so it must be started by
  the batch step itself — not from a transient `srun --overlap` step.
- **Node**: rootless install at `$HOME/opt/node/bin` (node 20 / npm 10), used only
  to build the web bundle.
- **`set -u` and the module system**: the cluster module init scripts reference
  unset variables, so `module load` must be bracketed by `set +u` / `set -u`.
  Without that, `cluster_up.sh` exits with no output at all.

## Troubleshooting

**Stale rootless-docker lock** after an unclean job exit:

```bash
rm -rf /raid/docker/tmp/xdg_runtime_dir_$(id -u)/dockerd-rootless
start_rootless_docker
```

**Ultra takes ~24 minutes to start cold** (image pull, ~328 GiB of weights, then
autotuning) and around 10 minutes warm. `/v1/health/ready` returns 503 until it
finishes; that is not a failure. Watch progress with `ultra_follow.sh`.

**`/raid` is node-local.** A model cache warmed on one node does nothing for a
job that lands elsewhere, which is why `ultra_serve.sbatch` is usually submitted
with `--nodelist=<node>` to reuse a warm cache.
