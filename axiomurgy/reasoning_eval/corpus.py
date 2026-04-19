"""Corpus loading: small, maintainable spell list with optional expectations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from axiomurgy.util import ROOT


def load_corpus(path: Path) -> Dict[str, Any]:
    """Load corpus JSON. Expected top-level: ``version`` (int), ``spells`` (list)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("corpus root must be an object")
    spells = raw.get("spells")
    if not isinstance(spells, list):
        raise ValueError("corpus.spells must be a list")
    return raw


def resolve_corpus_spell_path(entry: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Path:
    """Resolve ``path`` (repo-relative) or ``spell_path`` alias to absolute path."""
    root = repo_root or ROOT
    rel = entry.get("path") or entry.get("spell_path")
    if not rel or not isinstance(rel, str):
        raise ValueError("corpus entry requires string 'path'")
    p = (root / rel).resolve()
    return p


def normalize_corpus_entries(doc: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return a copy of each spell entry with resolved ``_resolved_path`` and family default."""
    out: List[Dict[str, Any]] = []
    for i, row in enumerate(doc.get("spells") or []):
        if not isinstance(row, dict):
            raise ValueError(f"corpus.spells[{i}] must be an object")
        rp = resolve_corpus_spell_path(row)
        merged = dict(row)
        merged["_resolved_path"] = str(rp)
        merged.setdefault("family", "unspecified")
        if "expect" in merged and merged["expect"] is not None and not isinstance(merged["expect"], dict):
            raise ValueError(f"corpus.spells[{i}].expect must be an object or omitted")
        out.append(merged)
    return out
