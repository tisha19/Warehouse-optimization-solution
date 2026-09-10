"""NVIDIA OpenShell governance layer for the MAIW warehouse deep agent.

Two halves:

* ``governor`` — the OpenShell Governor service (FastAPI). Loads the egress
  policy YAML, serves the admin console UI, holds per-user grants / pending
  approvals / the audit log, and reverse-proxies LLM + cuOpt + HTTP egress.
* ``governance`` — the in-process client the application uses at its egress
  chokepoints (LLM builder, MCP tool registry, Postgres connector, cuOpt
  clients) to ask the governor for a grant before every call.
"""
