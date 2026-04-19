"""Wyrd v1: append-only SQLite causal memory under <artifact-dir>/wyrd/graph.sqlite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .model import NODE_KINDS, WYRD_SCHEMA_VERSION

META_KEY_SCHEMA = "wyrd_schema_version"

DDL = """
CREATE TABLE IF NOT EXISTS wyrd_meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wyrd_nodes (
  node_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  run_id TEXT NOT NULL,
  spell_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  content_json TEXT NOT NULL,
  source_refs_json TEXT NOT NULL,
  tags_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wyrd_edges (
  edge_id TEXT PRIMARY KEY,
  src_node_id TEXT NOT NULL,
  dst_node_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  run_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wyrd_nodes_run ON wyrd_nodes(run_id);
CREATE INDEX IF NOT EXISTS idx_wyrd_nodes_spell ON wyrd_nodes(spell_name);
CREATE INDEX IF NOT EXISTS idx_wyrd_nodes_kind ON wyrd_nodes(kind);
CREATE INDEX IF NOT EXISTS idx_wyrd_nodes_created ON wyrd_nodes(created_at);
CREATE INDEX IF NOT EXISTS idx_wyrd_edges_run ON wyrd_edges(run_id);
CREATE INDEX IF NOT EXISTS idx_wyrd_edges_src ON wyrd_edges(src_node_id);
CREATE INDEX IF NOT EXISTS idx_wyrd_edges_dst ON wyrd_edges(dst_node_id);
"""


def wyrd_db_path(artifact_dir: Path) -> Path:
    return Path(artifact_dir) / "wyrd" / "graph.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    row = conn.execute("SELECT v FROM wyrd_meta WHERE k = ?", (META_KEY_SCHEMA,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO wyrd_meta (k, v) VALUES (?, ?)",
            (META_KEY_SCHEMA, WYRD_SCHEMA_VERSION),
        )
    conn.commit()


def _migrate_legacy_table_if_present(conn: sqlite3.Connection) -> None:
    """One-time migrate pre-v1 wyrd_nodes(id, kind, payload_json) into wyrd_nodes v1 shape."""
    info = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='wyrd_nodes'").fetchone()
    if not info:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wyrd_nodes)").fetchall()}
    if "payload_json" not in cols:
        return
    rows = conn.execute("SELECT id, kind, payload_json, created_ts FROM wyrd_nodes").fetchall()
    conn.execute("ALTER TABLE wyrd_nodes RENAME TO wyrd_nodes_legacy_pre_v1")
    conn.executescript(DDL)
    now = rows[0][3] if rows and rows[0][3] else "1970-01-01T00:00:00Z"
    for _id, kind, raw, ts in rows:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        ts_use = ts or now
        node_id = _stable_node_id("legacy_pre_v1", str(kind), str(_id), json.dumps(payload, sort_keys=True))
        conn.execute(
            """
            INSERT OR IGNORE INTO wyrd_nodes
            (node_id, kind, run_id, spell_name, created_at, content_json, source_refs_json, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                str(kind),
                "legacy_pre_v1",
                "",
                ts_use,
                json.dumps({"legacy_id": _id, "payload": payload}, sort_keys=True),
                json.dumps([], sort_keys=True),
                json.dumps(["migrated_pre_v1"], sort_keys=True),
            ),
        )
    conn.execute("DROP TABLE wyrd_nodes_legacy_pre_v1")
    conn.commit()


def _stable_node_id(run_id: str, kind: str, key: str, content_fingerprint: str = "") -> str:
    h = hashlib.sha256(f"{run_id}|{kind}|{key}|{content_fingerprint}".encode("utf-8")).hexdigest()[:32]
    return f"n_{h}"


def _stable_edge_id(run_id: str, src: str, dst: str, kind: str) -> str:
    h = hashlib.sha256(f"{run_id}|{src}|{dst}|{kind}".encode("utf-8")).hexdigest()[:32]
    return f"e_{h}"


def append_graph_snapshot(
    artifact_dir: Path,
    *,
    run_id: str,
    spell_name: str,
    nodes: Sequence[Dict[str, Any]],
    edges: Sequence[Dict[str, Any]],
    created_at: str,
) -> None:
    """Append nodes and edges (INSERT OR IGNORE by id). Must not raise to callers that need soft-fail."""
    path = wyrd_db_path(artifact_dir)
    conn = _connect(path)
    try:
        _migrate_legacy_table_if_present(conn)
        _ensure_schema(conn)
        for n in nodes:
            conn.execute(
                """
                INSERT OR IGNORE INTO wyrd_nodes
                (node_id, kind, run_id, spell_name, created_at, content_json, source_refs_json, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    n["node_id"],
                    n["kind"],
                    n.get("run_id", run_id),
                    n.get("spell_name", spell_name),
                    n.get("created_at", created_at),
                    json.dumps(n.get("content", {}), sort_keys=True),
                    json.dumps(n.get("source_refs", []), sort_keys=True),
                    json.dumps(n.get("tags", []), sort_keys=True),
                ),
            )
        for e in edges:
            conn.execute(
                """
                INSERT OR IGNORE INTO wyrd_edges
                (edge_id, src_node_id, dst_node_id, kind, run_id, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e["edge_id"],
                    e["src_node_id"],
                    e["dst_node_id"],
                    e["kind"],
                    e.get("run_id", run_id),
                    e.get("created_at", created_at),
                    json.dumps(e.get("metadata", {}), sort_keys=True),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def append_node(artifact_dir: Path, kind: str, payload: Dict[str, Any]) -> None:
    """Backward-compatible manual append (tests): stores one node; nonstandard kinds map to outcome + tag."""
    run_id = "legacy_manual_append"
    created_at = payload.get("created_at") or "1970-01-01T00:00:00Z"
    fp = json.dumps(payload, sort_keys=True)
    node_id = _stable_node_id(run_id, kind, "manual", fp)
    store_kind = kind if kind in NODE_KINDS else "outcome"
    content: Dict[str, Any] = dict(payload)
    if store_kind == "outcome" and kind not in NODE_KINDS:
        content["_nonstandard_kind"] = kind
    append_graph_snapshot(
        artifact_dir,
        run_id=run_id,
        spell_name=str(payload.get("spell_name", "")),
        nodes=[
            {
                "node_id": node_id,
                "kind": store_kind,
                "run_id": run_id,
                "spell_name": str(payload.get("spell_name", "")),
                "created_at": created_at,
                "content": content,
                "source_refs": [],
                "tags": ["legacy_append_node"],
            }
        ],
        edges=[],
        created_at=created_at,
    )


def query_recent_nodes(
    artifact_dir: Path,
    *,
    spell_name: Optional[str] = None,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    path = wyrd_db_path(artifact_dir)
    if not path.is_file():
        return []
    conn = _connect(path)
    try:
        _migrate_legacy_table_if_present(conn)
        _ensure_schema(conn)
        if spell_name:
            rows = conn.execute(
                """
                SELECT node_id, kind, run_id, spell_name, created_at, content_json, source_refs_json, tags_json
                FROM wyrd_nodes
                WHERE spell_name = ?
                ORDER BY created_at DESC, node_id DESC
                LIMIT ?
                """,
                (spell_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT node_id, kind, run_id, spell_name, created_at, content_json, source_refs_json, tags_json
                FROM wyrd_nodes
                ORDER BY created_at DESC, node_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        node_id, kind, run_id, sn, cat, cj, sr, tg = row
        try:
            content = json.loads(cj)
        except json.JSONDecodeError:
            content = {}
        try:
            source_refs = json.loads(sr)
        except json.JSONDecodeError:
            source_refs = []
        try:
            tags = json.loads(tg)
        except json.JSONDecodeError:
            tags = []
        out.append(
            {
                "node_id": node_id,
                "kind": kind,
                "run_id": run_id,
                "spell_name": sn,
                "created_at": cat,
                "content": content,
                "source_refs": source_refs,
                "tags": tags,
            }
        )
    return list(reversed(out))


def query_recent_edges_for_nodes(
    artifact_dir: Path,
    node_ids: Sequence[str],
    *,
    limit: int = 16,
) -> List[Dict[str, Any]]:
    if not node_ids:
        return []
    path = wyrd_db_path(artifact_dir)
    if not path.is_file():
        return []
    conn = _connect(path)
    try:
        _migrate_legacy_table_if_present(conn)
        _ensure_schema(conn)
        placeholders = ",".join("?" * len(node_ids))
        q = f"""
        SELECT edge_id, src_node_id, dst_node_id, kind, run_id, created_at, metadata_json
        FROM wyrd_edges
        WHERE src_node_id IN ({placeholders}) OR dst_node_id IN ({placeholders})
        ORDER BY created_at DESC, edge_id DESC
        LIMIT ?
        """
        params = list(node_ids) + list(node_ids) + [limit]
        rows = conn.execute(q, params).fetchall()
    finally:
        conn.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        eid, src, dst, kind, run_id, cat, mj = row
        try:
            metadata = json.loads(mj)
        except json.JSONDecodeError:
            metadata = {}
        out.append(
            {
                "edge_id": eid,
                "src_node_id": src,
                "dst_node_id": dst,
                "kind": kind,
                "run_id": run_id,
                "created_at": cat,
                "metadata": metadata,
            }
        )
    return list(reversed(out))


def query_prior_run_ids_for_spell(
    artifact_dir: Path,
    spell_name: str,
    *,
    exclude_run_id: Optional[str] = None,
    limit: int = 5,
) -> List[str]:
    path = wyrd_db_path(artifact_dir)
    if not path.is_file() or not spell_name:
        return []
    conn = _connect(path)
    try:
        _migrate_legacy_table_if_present(conn)
        _ensure_schema(conn)
        if exclude_run_id:
            rows = conn.execute(
                """
                SELECT run_id FROM (
                  SELECT run_id, MAX(created_at) AS mx FROM wyrd_nodes
                  WHERE spell_name = ? AND run_id != ?
                  GROUP BY run_id
                ) ORDER BY mx DESC LIMIT ?
                """,
                (spell_name, exclude_run_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT run_id FROM (
                  SELECT run_id, MAX(created_at) AS mx FROM wyrd_nodes
                  WHERE spell_name = ?
                  GROUP BY run_id
                ) ORDER BY mx DESC LIMIT ?
                """,
                (spell_name, limit),
            ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows if r and r[0]]


def count_rows(artifact_dir: Path) -> Tuple[int, int]:
    path = wyrd_db_path(artifact_dir)
    if not path.is_file():
        return (0, 0)
    conn = _connect(path)
    try:
        _migrate_legacy_table_if_present(conn)
        _ensure_schema(conn)
        n = conn.execute("SELECT COUNT(*) FROM wyrd_nodes").fetchone()[0]
        e = conn.execute("SELECT COUNT(*) FROM wyrd_edges").fetchone()[0]
        return (int(n), int(e))
    finally:
        conn.close()


def build_wyrd_hints(
    artifact_dir: Path,
    *,
    spell_name: str = "",
    current_run_id: Optional[str] = None,
    limit_nodes: int = 8,
    limit_edges: int = 16,
    limit_prior_runs: int = 5,
) -> Dict[str, Any]:
    """
    Bounded read-mostly hints for reasoning.experimental.wyrd_hints (not a raw graph dump).
    """
    path = wyrd_db_path(artifact_dir)
    if not path.is_file():
        return {
            "kind": "derived",
            "recent_nodes": [],
            "recent_edges": [],
            "related_prior_runs": [],
            "consistency_notes": ["no_wyrd_database"],
        }

    recent_full = query_recent_nodes(artifact_dir, spell_name=spell_name or None, limit=limit_nodes)
    recent_nodes: List[Dict[str, Any]] = []
    for n in recent_full:
        recent_nodes.append(
            {
                "node_id": n["node_id"],
                "kind": n["kind"],
                "run_id": n["run_id"],
                "spell_name": n["spell_name"],
                "summary": _summarize_node(n),
            }
        )
    nids = [n["node_id"] for n in recent_full]
    recent_edges_raw = query_recent_edges_for_nodes(artifact_dir, nids, limit=limit_edges)
    recent_edges: List[Dict[str, Any]] = []
    for e in recent_edges_raw:
        recent_edges.append(
            {
                "edge_id": e["edge_id"],
                "kind": e["kind"],
                "src_node_id": e["src_node_id"],
                "dst_node_id": e["dst_node_id"],
                "run_id": e["run_id"],
            }
        )

    prior: List[str] = []
    notes: List[str] = []
    if spell_name:
        prior = query_prior_run_ids_for_spell(
            artifact_dir, spell_name, exclude_run_id=current_run_id, limit=limit_prior_runs
        )
        if prior:
            notes.append(f"prior_wyrd_runs_for_spell:{len(prior)}")
        else:
            notes.append("no_prior_matching_context")
    else:
        notes.append("no_spell_filter_for_related_runs")

    if not recent_nodes and not recent_edges:
        notes.append("empty_graph")

    return {
        "kind": "derived",
        "recent_nodes": recent_nodes,
        "recent_edges": recent_edges,
        "related_prior_runs": [{"run_id": r} for r in prior],
        "consistency_notes": notes,
    }


def _summarize_node(n: Dict[str, Any]) -> str:
    kind = n.get("kind", "")
    c = n.get("content") or {}
    if kind == "telos":
        fc = str(c.get("final_cause", ""))[:120]
        return f"telos:{fc}"
    if kind == "governor_tradeoff":
        return f"tradeoff:{str(c.get('resolution', c.get('axis_a', '')))[:120]}"
    if kind == "dialectic_episode":
        return f"episode:{str(c.get('thesis_summary', ''))[:80]}"
    if kind == "correspondence_cluster":
        return f"cluster:{c.get('cluster_id', '')}:{c.get('motif', '')}"
    if kind == "friction_bottleneck":
        return f"bottleneck:{c.get('step_id', '')}"
    if kind == "outcome":
        return f"outcome:{str(c.get('status', ''))}"
    if kind == "review_bundle_ref":
        return f"review:{str(c.get('path', ''))[:120]}"
    if kind == "witness_ref":
        return f"witness:{str(c.get('relative_path', ''))[:120]}"
    return f"{kind}:{str(c)[:80]}"


def stable_node_id(run_id: str, kind: str, key: str, content_fingerprint: str = "") -> str:
    return _stable_node_id(run_id, kind, key, content_fingerprint)


def stable_edge_id(run_id: str, src: str, dst: str, kind: str) -> str:
    return _stable_edge_id(run_id, src, dst, kind)


# Re-export kinds for snapshot module
__all__ = [
    "append_graph_snapshot",
    "append_node",
    "build_wyrd_hints",
    "count_rows",
    "query_prior_run_ids_for_spell",
    "query_recent_edges_for_nodes",
    "query_recent_nodes",
    "stable_edge_id",
    "stable_node_id",
    "wyrd_db_path",
]
