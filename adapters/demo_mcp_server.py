#!/usr/bin/env python3
"""Demo MCP server for Axiomurgy.

Features:
- resources/list over bundled primer documents in the repo
- resources/read for those documents
- tools/call for stage_note, delete_note, and extract_headlines

Transport: stdio with newline-delimited JSON-RPC 2.0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "axiomurgy_workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
PRIMER_DIR = ROOT / "primers"


def list_primer_files() -> List[Path]:
    return sorted(PRIMER_DIR.glob("primer_*.txt"))


def primer_resources() -> List[Dict[str, Any]]:
    resources = []
    for idx, path in enumerate(list_primer_files(), start=1):
        resources.append(
            {
                "uri": f"upload://primer/{idx}",
                "name": path.name,
                "title": f"Primer transcript {idx}",
                "description": f"Uploaded transcript resource backed by {path}",
                "mimeType": "text/plain",
            }
        )
    return resources


def path_for_uri(uri: str) -> Path:
    match = re.fullmatch(r"upload://primer/(\d+)", uri)
    if not match:
        raise ValueError(f"Unknown resource URI: {uri}")
    index = int(match.group(1)) - 1
    files = list_primer_files()
    if index < 0 or index >= len(files):
        raise ValueError(f"Resource index out of range: {uri}")
    return files[index]


def response(message_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error(message_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def handle_request(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    method = payload.get("method")
    message_id = payload.get("id")
    params = payload.get("params", {}) or {}

    if method == "initialize":
        return response(
            message_id,
            {
                "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                "serverInfo": {"name": "axiomurgy-demo-mcp", "version": "0.6.0"},
                "capabilities": {"resources": {}, "tools": {}},
            },
        )

    if method == "resources/list":
        return response(message_id, {"resources": primer_resources()})

    if method == "resources/read":
        uri = str(params.get("uri", ""))
        path = path_for_uri(uri)
        return response(
            message_id,
            {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/plain",
                        "text": path.read_text(encoding="utf-8"),
                    }
                ]
            },
        )

    if method == "tools/list":
        return response(
            message_id,
            {
                "tools": [
                    {
                        "name": "stage_note",
                        "title": "Stage workspace note",
                        "description": "Write a text note into the Axiomurgy workspace.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["path", "content"],
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"}
                            }
                        }
                    },
                    {
                        "name": "delete_note",
                        "title": "Delete workspace note",
                        "description": "Delete a previously staged workspace note.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["path"],
                            "properties": {
                                "path": {"type": "string"}
                            }
                        }
                    },
                    {
                        "name": "extract_headlines",
                        "title": "Extract rough headlines",
                        "description": "Return the first non-empty line from each text block.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["texts"],
                            "properties": {
                                "texts": {"type": "array", "items": {"type": "string"}}
                            }
                        }
                    }
                ]
            },
        )

    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments", {}) or {}
        if name == "stage_note":
            rel = Path(str(arguments["path"]))
            target = (WORKSPACE / rel).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(arguments["content"]), encoding="utf-8")
            return response(
                message_id,
                {
                    "content": [
                        {"type": "text", "text": f"Staged note at {target}"},
                        {
                            "type": "resource_link",
                            "uri": f"file://{target}",
                            "name": target.name,
                            "mimeType": "text/markdown"
                        }
                    ],
                    "structuredContent": {"path": str(target), "status": "written"}
                },
            )
        if name == "delete_note":
            rel = Path(str(arguments["path"]))
            target = (WORKSPACE / rel).resolve()
            existed = target.exists()
            if existed:
                target.unlink()
            return response(
                message_id,
                {
                    "content": [{"type": "text", "text": f"Deleted={existed} {target}"}],
                    "structuredContent": {"path": str(target), "deleted": existed}
                },
            )
        if name == "extract_headlines":
            texts = list(arguments.get("texts", []))
            headlines = []
            for idx, text in enumerate(texts, start=1):
                first = next((line.strip() for line in str(text).splitlines() if line.strip()), "(empty)")
                headlines.append(f"Source {idx}: {first[:120]}")
            return response(
                message_id,
                {
                    "content": [{"type": "text", "text": "\n".join(headlines)}],
                    "structuredContent": {"headlines": headlines}
                },
            )
        return error(message_id, -32602, f"Unknown tool: {name}")

    if method == "notifications/initialized":
        return None

    return error(message_id, -32601, f"Unknown method: {method}")


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = handle_request(payload)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
