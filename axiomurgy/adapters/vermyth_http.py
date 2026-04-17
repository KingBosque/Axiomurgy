"""HTTP client for Vermyth localhost adapter (JSON only; no vermyth imports)."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests


class VermythHttpError(RuntimeError):
    """Raised when the Vermyth HTTP adapter returns an error or unexpected payload."""


class VermythHttpClient:
    """Thin client mirroring documented MCP tool shapes over POST /tools/<name> and POST /arcane/recommend."""

    def __init__(self, base_url: str, *, timeout_s: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_s = timeout_s

    def _post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        r = requests.post(url, json=body, timeout=self.timeout_s)
        if r.status_code >= 400:
            raise VermythHttpError(f"HTTP {r.status_code} from {url}: {r.text[:500]}")
        try:
            out = r.json()
        except ValueError as exc:
            raise VermythHttpError(f"invalid JSON from {url}") from exc
        if not isinstance(out, dict):
            raise VermythHttpError("expected JSON object response")
        return out

    def arcane_recommend(
        self, *, skill_id: str, input_: str, min_strength: Optional[float] = None
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"skill_id": skill_id, "input": input_}
        if min_strength is not None:
            body["min_strength"] = min_strength
        return self._post_json("arcane/recommend", body)

    def compile_program(self, program: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json("tools/compile_program", {"program": program})

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json("tools/decide", payload)

    @staticmethod
    def timed_call(fn: Any) -> tuple[Any, float]:
        t0 = time.perf_counter()
        out = fn()
        ms = (time.perf_counter() - t0) * 1000.0
        return out, ms
