"""Probe every external dependency and report what is actually wired.

Run:
    python -m tools.wiring_report
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from services.config import ProductionConfig


@dataclass
class Probe:
    name: str
    url: str
    method: str
    path: str
    api_key: str
    required: bool
    degrades_to: str
    payload: Optional[Dict[str, Any]] = None


@dataclass
class Result:
    name: str
    url: str
    wired: bool
    detail: str
    degrades_to: str
    required: bool


def _request(url: str, method: str, api_key: str, payload: Optional[Dict[str, Any]], timeout: float) -> Tuple[bool, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        # 404 means something answered but does not expose the expected contract.
        reachable = exc.code in {200, 201, 204, 400, 401, 403, 405, 422}
        suffix = "" if reachable else " (listener answered, but not the expected service/path)"
        return reachable, f"HTTP {exc.code}{suffix}"
    except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
        return False, f"unreachable ({getattr(exc, 'reason', exc)})"


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def build_probes(config: ProductionConfig) -> List[Probe]:
    return [
        Probe("Self-hosted NIM (supervisor)", config.nim_base_url, "GET", "models", config.nim_api_key, False, "NVIDIA cloud NIM"),
        Probe("NVIDIA cloud NIM (fallback)", config.nim_cloud_base_url, "GET", "models", config.nim_cloud_api_key, False, "hard failure if self-hosted is also down"),
        Probe("cuOpt slotting adapter", config.cuopt_url, "GET", "health", config.nim_api_key, False, "deterministic local planner"),
        Probe("NeMo Guardrails", config.guardrails_url, "GET", "health", config.nim_api_key, False, "local baseline policy"),
        Probe("OpenShell", config.openshell_url, "GET", "health", config.nim_api_key, False, "local allowlist policy"),
        Probe("WMS", os.getenv("WMS_URL", ""), "POST", "warehouse/snapshot", "", False, "synthetic warehouse data", {}),
        Probe("ERP", os.getenv("ERP_URL", ""), "POST", "erp/sku-master", "", False, "synthetic SKU master", {}),
        Probe("Forecast", os.getenv("FORECAST_URL", ""), "POST", "forecast", "", False, "synthetic forecast", {"horizon_days": 1}),
    ]


def run(timeout: float = 5.0) -> List[Result]:
    config = ProductionConfig.from_env()
    results: List[Result] = []
    for probe in build_probes(config):
        if not probe.url:
            results.append(Result(probe.name, "(not configured)", False, "no URL set", probe.degrades_to, probe.required))
            continue
        ok, detail = _request(_join(probe.url, probe.path), probe.method, probe.api_key, probe.payload, timeout)
        results.append(Result(probe.name, probe.url, ok, detail, probe.degrades_to, probe.required))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Report which external services are wired")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    results = run(args.timeout)
    if args.json:
        print(json.dumps([result.__dict__ for result in results], indent=2))
        return

    width = max(len(result.name) for result in results)
    print("\nDependency wiring report")
    print("=" * (width + 58))
    for result in results:
        status = "WIRED    " if result.wired else "NOT WIRED"
        print(f"{result.name.ljust(width)}  {status}  {result.url}")
        print(f"{' '.ljust(width)}  {result.detail}")
        if not result.wired:
            print(f"{' '.ljust(width)}  falls back to: {result.degrades_to}")
    print("=" * (width + 58))
    wired = sum(1 for result in results if result.wired)
    print(f"{wired}/{len(results)} dependencies reachable\n")


if __name__ == "__main__":
    main()
