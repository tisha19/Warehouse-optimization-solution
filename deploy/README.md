# WarehouseIQ on Curiosity v2

WarehouseIQ runs as two halves:

- **Models — hosted by NVIDIA.** Nemotron 3 Ultra orchestrates and Nemotron 3.5
  Lightning runs the specialists, both at `https://inference-api.nvidia.com/v1`.
  Nothing to start, nothing to load, no GPU.
- **Services — ours, on a DGX-B300 node.** cuOpt, the cuOpt slotting adapter,
  NeMo Guardrails, the OpenShell governor and the WarehouseIQ UI/API.

Only cuOpt needs a GPU, so the whole stack fits in **one** of the team's four.

## Start everything

```bash
cd ~/gsh-team07/Warehouse-optimization-solution
./deploy/runall.sh
```

`runall.sh` is the only command you normally need. It checks the hosted
endpoint, reports what is already up, submits the stack job if it is missing,
follows the boot log until every service answers its health check, then prints
the final status and how to reach it. It is safe to re-run: a healthy stack is
left alone rather than restarted.

Arguments are passed through to `sbatch`, so to reuse a warm image cache:

```bash
./deploy/runall.sh --nodelist=dgx02
```

A cold start takes about 80 seconds once the images are cached.

## Reach the services from your laptop

```bash
./deploy/forwardallports.sh <your-access-key>
```

Run this **on your own machine**, not on the cluster — SSH port forwarding is
set up by the client, so every team member runs it themselves with their own
access key. Leave it running; Ctrl-C closes the tunnels.

It reads the stack owner's state file to discover which node the stack landed on
and which ports it bound, then forwards all of them in a single SSH session:

| Service | Local URL |
|---|---|
| WarehouseIQ UI | <http://127.0.0.1:28090> |
| cuOpt solver | <http://127.0.0.1:25000> |
| cuOpt adapter | <http://127.0.0.1:28002> |
| NeMo Guardrails | <http://127.0.0.1:28003> |
| OpenShell governor | <http://127.0.0.1:28004> |

Options:

| Flag | Why |
|---|---|
| `--owner <user>` | Forward someone else's stack. Defaults to the shared one. |
| `--offset <n>` | Shift every local port by `n` when something on your machine already holds one. Remote ports are unchanged. |
| `--print` | Show the ssh command and the URL table, then exit. |

Works with any OpenSSH client: macOS, Linux, WSL, or Git Bash on Windows.

## Ports

Defaults are a private **28xxx** block rather than the obvious 8000/8002/8004.
Several of us share a node and run this same stack, and the obvious ports are
already taken. Worse than failing to bind, `cluster_up.sh`'s `skip_if_up` check
would see a teammate's healthy service, skip starting ours, and silently wire
our app to their containers. Override any of `CUOPT_PORT`, `ADAPTER_PORT`,
`GUARDRAILS_PORT`, `OPENSHELL_PORT`, `APP_PORT` if you need to move.

## Sharing the cluster

The checkout lives on team-shared storage and several of us run these scripts,
so anything with a fixed name is contended. What is already handled:

- **State** is per-user: `deploy/state/stack.$USER.env`.
- **Logs** are per-user: `/tmp/$USER-warehouse/`. A fixed `/tmp/governor.log`
  belongs to whoever started first and denies everyone else.
- **`.env`** is shared and gets rewritten by whoever runs `cluster_up.sh` last,
  so the script also exports the URLs into the environment of the processes it
  starts, with `WAREHOUSE_ENV_PRECEDENCE=process`. Process env beats `.env`.

Before assuming the GPU quota is the problem, check who is holding what:

```bash
squeue -q gsh-team07 -O JobID:8,UserName:12,Name:18,State:10,NodeList:10,tres-per-node:12
```

## Scripts

| Script | Run from | What it does |
|---|---|---|
| `runall.sh` | login node | Status → start what is missing → follow logs → final status → URL. The normal entry point. |
| `forwardallports.sh` | **your laptop** | Forwards every service to localhost in one SSH session. |
| `slurm_stack.sbatch` | `sbatch` | Holds the allocation and re-runs `cluster_up.sh` if containers vanish. Requests 1 GPU for 30 days. |
| `cluster_up.sh` | compute node | Brings the stack up idempotently: rootless docker, images, containers, web bundle, Guardrails config. Called by the sbatch job; safe to re-run. `FORCE=1` recreates everything. |
| `stack_status.sh` | login node | Per-service health plus the Slurm queue. Read-only, never starts anything. |
| `stack_verify.sh` | login node | End-to-end check: drives a run through the governor and the approval path. |
| `stack_down.sh` | login node | Cancels the stack job and releases the GPU. |
| `redeploy_adapter.sh` | `srun --overlap` | Rebuilds and restarts the cuOpt adapter after a code change. |
| `hosted_ultra_test.py` | anywhere with `.env` | Four checks against a hosted model — model list, completion, tool calling, streamed reasoning. Takes an optional model id. |
| `hosted_models.py` | anywhere with `.env` | Lists the hosted catalogue, optionally filtered by substring. |
| `show_env.py` / `set_env.py` | anywhere | Read `.env` with secrets masked / upsert a key read from stdin, so secrets never reach the command line or the shell history. |
| `check_versions.sh` | login node | Compares installed agent-stack versions against the latest on PyPI. |
| `fix_line_endings.py` | anywhere | Normalises CRLF **and bare CR** after any `scp` from Windows. A stray `\r` on a heredoc terminator makes bash report only `unexpected end of file`. |
| `guardrails_warehouse_config.json` | — | The rails config. `cluster_up.sh` rewrites its model id to the specialist model in use. |
| `openshell/` | — | Governor policy and sandbox definitions. |

## Restarting and resuming

| Situation | What to do |
|---|---|
| Anything is down | `./deploy/runall.sh` |
| Job `RUNNING`, one container died | The job re-runs `cluster_up.sh` itself within a minute. To force it: `srun --jobid=<ID> --overlap ./deploy/cluster_up.sh` |
| Adapter code changed | `srun --jobid=<ID> --overlap bash deploy/redeploy_adapter.sh` |
| UI code changed | Rebuild the bundle, then `srun --jobid=<ID> --overlap pkill -f 'showcase_server\.py'` — the monitor restarts it within ~60s |
| You lost your SSH session | Nothing to do. Batch jobs are unaffected; reconnect and run `./deploy/stack_status.sh` |

The job requests the full 30 days the QoS allows, because **a running job's time
limit cannot be raised by its owner** — `scontrol update` returns
`Access/permission denied`. It has to be right at submission.

## Environment notes

Several things are missing from a non-interactive `PATH` and fail silently:

- **Slurm**: `export PATH=/cm/local/apps/slurm/current/bin:$PATH` and
  `export SLURM_CONF=/cm/shared/apps/slurm/etc/slurm/slurm.conf`.
- **Rootless docker**: `/cm/shared/apps/rootless-docker/bin`, per-user socket at
  `unix:///raid/docker/tmp/xdg_runtime_dir_$(id -u)/docker.sock`. The daemon dies
  with the job step that started it, so it must be started by the batch step
  itself — never from a transient `srun --overlap` step.
- **Node**: rootless install at `$HOME/opt/node/bin`, used only to build the bundle.
- **`set -u` and the module system**: module init scripts reference unset
  variables, so `module load` must be bracketed by `set +u` / `set -u`. Without
  that, `cluster_up.sh` exits with no output at all.
- **`/raid` is node-local.** A warm image cache does nothing for a job that lands
  elsewhere, which is why `--nodelist=` is worth passing.

## Troubleshooting

**GPU containers will not start**, with:

```
runc create failed: ... failed to fulfil mount request:
open /run/nvidia-persistenced/socket: no such file or directory
```

The node ships a stale CDI spec referencing a socket that no longer exists, and
it breaks **both** `--gpus "device=N"` and `--device nvidia.com/gpu=N`. Use
`--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=<GPU-UUID>` instead, which
`cluster_up.sh` now does. UUIDs come from
`nvidia-smi --query-gpu=uuid --format=csv,noheader`, and only the job's own GPUs
are visible, so a UUID cannot accidentally name someone else's.

**Why the NIMs are not self-hosted.** The Lightning NIM loads its weights, then
logs `Your GPU does not have native support for FP4 computation` on a B300 —
which does support FP4 natively — falls back to Marlin kernels and the engine
core dies with `Engine core initialization failed`. Its vLLM build does not
recognise this compute capability. NVIDIA hosts both models, so the stack uses
those and leaves the GPUs for cuOpt and the rest of the team.

**A reasoning model returns empty content** with `finish_reason: length`. The
token budget has to cover the reasoning tokens too: Lightning spent 152
completion tokens to answer `OK`. Give `max_tokens` real headroom.

**Stale rootless-docker lock** after an unclean job exit:

```bash
rm -rf /raid/docker/tmp/xdg_runtime_dir_$(id -u)/dockerd-rootless
start_rootless_docker
```
