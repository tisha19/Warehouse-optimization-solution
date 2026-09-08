"""HTTP server connecting the showcase UI to the production LangGraph workflow."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from showcase.controller import ShowcaseController

ROOT = Path(__file__).parent
CONTROLLER: ShowcaseController | None = None


class ShowcaseHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(ROOT / "showcase"), **kwargs)

    @property
    def controller(self) -> ShowcaseController:
        if CONTROLLER is None:
            raise RuntimeError("Showcase controller is not initialized")
        return CONTROLLER

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self.execute(self.controller.state)
            return
        super().do_GET()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self.send_json({"error": f"Invalid JSON: {exc}"}, 400)
            return
        if self.path == "/api/replan":
            self.execute(lambda: self.controller.replan(payload))
        elif self.path == "/api/approve":
            self.execute(lambda: self.controller.approve(str(payload.get("move_id", ""))))
        else:
            self.send_error(404)

    def execute(self, operation) -> None:
        try:
            self.send_json(operation())
        except Exception as exc:
            self.send_json({"error": str(exc), "missing_configuration": ShowcaseController.missing_configuration()}, 502)

    def send_json(self, body: Dict[str, Any], status: int = 200) -> None:
        data = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the workflow-backed warehouse planner showcase")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--seed", type=int, default=7, help="Synthetic enterprise data seed")
    args = parser.parse_args()
    missing = ShowcaseController.missing_configuration()
    if missing:
        print("Warning: live workflow configuration is incomplete: " + ", ".join(missing))
    global CONTROLLER
    CONTROLLER = ShowcaseController(seed=args.seed)
    server = ThreadingHTTPServer((args.host, args.port), ShowcaseHandler)
    print(f"Workflow-backed warehouse planner showcase: http://{args.host}:{args.port}")
    print("Enterprise inputs: synthetic WMS/ERP/forecast data; planning: production LangGraph with configured NVIDIA and cuOpt services")
    server.serve_forever()


if __name__ == "__main__":
    main()
