"""Optional SQLite-backed Wyrd graph (opt-in via AXIOMURGY_WYRD)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

SCHEMA = """
CREATE TABLE IF NOT EXISTS wyrd_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_ts TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def wyrd_db_path(artifact_dir: Path) -> Path:
    return Path(artifact_dir) / "wyrd" / "graph.sqlite"


def _ensure_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def append_node(artifact_dir: Path, kind: str, payload: Dict[str, Any]) -> None:
    """Append a causal-memory node (for future execution hooks)."""
    path = wyrd_db_path(artifact_dir)
    _ensure_db(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO wyrd_nodes (kind, payload_json) VALUES (?, ?)",
            (kind, json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()


def read_wyrd_hints(artifact_dir: Path, *, limit: int = 8) -> List[Dict[str, Any]]:
    """Recent node summaries for plan/describe; empty if DB missing or empty."""
    path = wyrd_db_path(artifact_dir)
    if not path.is_file():
        return []
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT id, kind, payload_json FROM wyrd_nodes ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        nid, kind, raw = row
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        out.append({"id": nid, "kind": kind, "payload": payload})
    return list(reversed(out))
