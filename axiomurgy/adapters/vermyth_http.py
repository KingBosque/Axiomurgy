"""HTTP client for Vermyth localhost adapter (JSON only; no vermyth imports)."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests


class VermythHttpError(RuntimeError):
    """Raised when the Vermyth HTTP adapter returns an error or unexpected payload."""


def _env_http_token() -> Optional[str]:
    """Bearer token for Vermyth HTTP when VERMYTH_HTTP_TOKEN / AXIOMURGY_VERMYTH_HTTP_TOKEN is set."""
    for key in ("AXIOMURGY_VERMYTH_HTTP_TOKEN", "VERMYTH_HTTP_TOKEN"):
        v = os.environ.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


class VermythHttpClient:
    """Thin client mirroring documented MCP tool shapes over POST /tools/<name> and POST /arcane/recommend."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 5.0,
        http_token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_s = timeout_s
        self._http_token = http_token if http_token is not None else _env_http_token()

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._http_token:
            h["Authorization"] = f"Bearer {self._http_token}"
        return h

    def _post_json(
        self,
        path: str,
        body: Dict[str, Any],
        *,
        unwrap_tool_result: bool = False,
    ) -> Dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        r = requests.post(url, json=body, headers=self._headers(), timeout=self.timeout_s)
        if r.status_code >= 400:
            raise VermythHttpError(f"HTTP {r.status_code} from {url}: {r.text[:500]}")
        try:
            out = r.json()
        except ValueError as exc:
            raise VermythHttpError(f"invalid JSON from {url}") from exc
        if not isinstance(out, dict):
            raise VermythHttpError("expected JSON object response")
        if unwrap_tool_result:
            inner = out.get("result")
            if isinstance(inner, dict):
                return inner
        return out

    def arcane_recommend(
        self, *, skill_id: str, input_: Dict[str, Any], min_strength: Optional[float] = None
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"skill_id": skill_id, "input": input_}
        if min_strength is not None:
            body["min_strength"] = min_strength
        return self._post_json("arcane/recommend", body, unwrap_tool_result=False)

    def compile_program(self, program: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json("tools/compile_program", {"program": program}, unwrap_tool_result=True)

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._post_json("tools/decide", payload, unwrap_tool_result=True)

    @staticmethod
    def timed_call(fn: Any) -> tuple[Any, float]:
        t0 = time.perf_counter()
        out = fn()
        ms = (time.perf_counter() - t0) * 1000.0
        return out, ms
