#!/usr/bin/env python3
"""Tiny local HTTP server for OpenAPI call demos."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

PORT = int(os.environ.get("AXIOMURGY_ISSUE_PORT", "8942"))
DB_PATH = Path(os.environ.get("AXIOMURGY_ISSUE_DB", "axiomurgy_mock_issues.json"))


def load_db() -> Dict[str, Any]:
    if not DB_PATH.exists():
        return {"next_id": 1, "tickets": {}}
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


def save_db(db: Dict[str, Any]) -> None:
    DB_PATH.write_text(json.dumps(db, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "AxiomurgyMockIssueServer/0.2"

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/tickets":
            self._send(404, {"error": "not found"})
            return
        data = self._read_json()
        db = load_db()
        ticket_id = str(db["next_id"])
        db["next_id"] += 1
        ticket = {
            "id": ticket_id,
            "title": data.get("title", "Untitled"),
            "description": data.get("description", ""),
            "labels": data.get("labels", []),
        }
        db["tickets"][ticket_id] = ticket
        save_db(db)
        self._send(201, ticket)

    def do_GET(self) -> None:  # noqa: N802
        if not self.path.startswith("/tickets/"):
            self._send(404, {"error": "not found"})
            return
        ticket_id = self.path.rsplit("/", 1)[-1]
        db = load_db()
        ticket = db["tickets"].get(ticket_id)
        if ticket is None:
            self._send(404, {"error": "not found", "id": ticket_id})
            return
        self._send(200, ticket)

    def do_DELETE(self) -> None:  # noqa: N802
        if not self.path.startswith("/tickets/"):
            self._send(404, {"error": "not found"})
            return
        ticket_id = self.path.rsplit("/", 1)[-1]
        db = load_db()
        existed = ticket_id in db["tickets"]
        if existed:
            db["tickets"].pop(ticket_id, None)
            save_db(db)
        self._send(200, {"deleted": existed, "id": ticket_id})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
