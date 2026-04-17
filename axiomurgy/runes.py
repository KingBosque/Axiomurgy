"""Rune registry and MCP client; rune handler implementations live in legacy."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable, Dict, List, Sequence


class RuneRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[..., Any]] = {}
        self._capability_map: Dict[str, str] = {}

    def register(self, name: str, capability: str):
        def decorator(func: Callable[..., Any]):
            self._handlers[name] = func
            self._capability_map[name] = capability
            return func

        return decorator

    def handler_for(self, name: str) -> Callable[..., Any]:
        if name not in self._handlers:
            raise KeyError(f"Unknown rune: {name}")
        return self._handlers[name]

    def required_capability(self, name: str) -> str:
        if name not in self._capability_map:
            raise KeyError(f"Unknown rune: {name}")
        return self._capability_map[name]


REGISTRY = RuneRegistry()


class MCPClient:
    """JSON-RPC MCP client over stdin/stdout. Imports legacy lazily to avoid import cycles."""

    def __init__(self, cmd: Sequence[str]) -> None:
        from . import legacy as L

        self.cmd = list(cmd)
        self._id = 0
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": L.MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "axiomurgy", "version": L.VERSION},
            },
        )
        self.notify("notifications/initialized", {})

    def _write(self, payload: Dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _read(self) -> Dict[str, Any]:
        from . import legacy as L

        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise L.StepExecutionError("MCP server closed unexpectedly")
        return json.loads(line)

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        from . import legacy as L

        self._id += 1
        self._write({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        response = self._read()
        if "error" in response:
            raise L.StepExecutionError(f"MCP error for {method}: {response['error']}")
        return response.get("result", {})

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def list_resources(self) -> List[Dict[str, Any]]:
        return list(self.request("resources/list", {}).get("resources", []))

    def read_resource(self, uri: str) -> List[Dict[str, Any]]:
        return list(self.request("resources/read", {"uri": uri}).get("contents", []))

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)


def __getattr__(name: str):
    if name.startswith("rune_") or name in ("coerce_text", "target_label"):
        from . import legacy as L

        return getattr(L, name)
    raise AttributeError(name)


__all__ = [
    "MCPClient",
    "RuneRegistry",
    "REGISTRY",
]
