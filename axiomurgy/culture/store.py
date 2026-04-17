"""SQLite-backed culture catalog (read-heavy; optional describe hints)."""

from __future__ import annotations

import os
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryTier(str, Enum):
    CANON = "canon"
    CUSTOM = "custom"
    FOLKLORE = "folklore"
    QUARANTINE = "quarantine"


def _default_db_path() -> Path:
    raw = os.environ.get("AXIOMURGY_CULTURE_DB")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()
    return Path(os.environ.get("TEMP", os.getcwd())) / "axiomurgy_culture.sqlite3"


class CultureStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path))
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                  id TEXT PRIMARY KEY,
                  tier TEXT NOT NULL,
                  content TEXT NOT NULL,
                  tags TEXT,
                  sha256 TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()
        return self._conn

    def list_recent(self, *, tier: Optional[MemoryTier] = None, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._ensure()
        if tier is not None:
            cur = conn.execute(
                "SELECT id, tier, substr(content,1,400), tags, sha256, created_at FROM memory WHERE tier = ? ORDER BY created_at DESC LIMIT ?",
                (tier.value, limit),
            )
        else:
            cur = conn.execute(
                "SELECT id, tier, substr(content,1,400), tags, sha256, created_at FROM memory ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "id": r[0],
                    "tier": r[1],
                    "preview": r[2],
                    "tags": r[3],
                    "sha256": r[4],
                    "created_at": r[5],
                }
            )
        return rows


def open_culture_store(path: Optional[Path] = None) -> CultureStore:
    return CultureStore(path or _default_db_path())


def culture_hints_for_describe() -> Optional[Dict[str, Any]]:
    if os.environ.get("AXIOMURGY_CULTURE", "").strip() not in ("1", "true", "yes"):
        return None
    store = open_culture_store()
    if not store.path.exists():
        return {"enabled": True, "records": [], "note": "culture DB not initialized"}
    return {
        "enabled": True,
        "db_path": str(store.path),
        "records": store.list_recent(limit=12),
    }
