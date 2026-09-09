"""HTTP server behind the WarehouseIQ UI."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict

from showcase.controller import ShowcaseController

ROOT = Path(__file__).parent
CONTROLLER: ShowcaseController | None = None
# Client-side routes have to fall through to the shell document.
APP_ROUTES = {"/", "/orchestration", "/plan", "/openshell"}


class WarehouseIQHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(ROOT / "showcase"), **kwargs)

    @property
    def controller(self) -> ShowcaseController:
        if CONTROLLER is None:
            raise RuntimeError("WarehouseIQ controller is not initialised")
        return CONTROLLER

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        routes: Dict[str, Callable[[], Any]] = {
            "/api/dashboard": self.controller.dashboard,
            "/api/run": self.controller.run,
            "/api/plan/commit": self.controller.commit_status,
            "/api/openshell": self.controller.openshell_overview,
            "/api/telemetry": self.controller.telemetry,
        }
        if path in routes:
            self.execute(routes[path])
            return
        if path in APP_ROUTES:
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self.send_json({"error": f"Invalid JSON: {exc}"}, 400)
            return
        path = self.path.split("?")[0]
        if path == "/api/run/start":
            self.execute(self.controller.start_run)
        elif path == "/api/reset":
            self.execute(self.controller.reset)
        elif path == "/api/plan/decide":
            self.execute(lambda: self.controller.decide(str(payload.get("move_id", "")), str(payload.get("decision", ""))))
        elif path == "/api/plan/commit":
            self.execute(self.controller.commit)
        elif path == "/api/openshell/resolve":
            self.execute(lambda: self.controller.resolve_request(str(payload.get("request_id", "")), bool(payload.get("approve", True))))
        elif path == "/api/openshell/revoke":
            self.execute(lambda: self.controller.revoke_grant(str(payload.get("user", "")), str(payload.get("service", ""))))
        else:
            self.send_error(404)

    def execute(self, operation: Callable[[], Any]) -> None:
        try:
            self.send_json(operation())
        except Exception as exc:
            # The UI shows the failure; it never substitutes invented data.
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 502)

    def end_headers(self) -> None:
        # The UI is redeployed in place, so a cached app.js must never win.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def send_json(self, body: Any, status: int = 200) -> None:
        data = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the WarehouseIQ decision copilot")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--seed", default=None, help="Synthetic enterprise data seed, or 'random'")
    args = parser.parse_args()
    global CONTROLLER
    CONTROLLER = ShowcaseController(seed=args.seed)
    server = ThreadingHTTPServer((args.host, args.port), WarehouseIQHandler)
    print(f"WarehouseIQ: http://{args.host}:{args.port}")
    for service in CONTROLLER.service_status():
        print(f"  {service['name']:<30} {service['endpoint']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
