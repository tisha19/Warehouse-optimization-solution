"""Shared JSON-over-HTTP transport for production service adapters."""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping


class ServiceError(RuntimeError):
    """Raised when a configured production service cannot complete a request."""


class JsonHttpClient:
    def __init__(self, base_url: str, timeout: float = 30, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key

    def post(self, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        url = self.base_url + "/" + path.lstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The body carries the reason the service refused; without it the
            # caller only sees a status code.
            detail = exc.read().decode("utf-8", "replace")[:400].strip()
            raise ServiceError(f"POST {url} failed: {exc} {detail}".strip()) from exc
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ServiceError(f"POST {url} failed: {exc}") from exc
