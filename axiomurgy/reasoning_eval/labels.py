"""Optional sidecar labels for human review (never required)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping


def load_labels(path: Path) -> Dict[str, Dict[str, Any]]:
    """
    Load labels JSON. Supported shapes:

    - ``{ "labels": [ { "spell_path": "...", "human_preferred_candidate_kind": "...", ... }, ... ] }``
    - or a bare list (treated as labels array)

    Keys are normalized by ``spell_path`` (as given; match capture ``spell_path`` strings).
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows: list
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict) and isinstance(raw.get("labels"), list):
        rows = raw["labels"]
    else:
        raise ValueError("labels file must be a list or { \"labels\": [...] }")

    out: Dict[str, Dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"labels[{i}] must be an object")
        sp = row.get("spell_path")
        if not sp:
            raise ValueError(f"labels[{i}] missing spell_path")
        out[str(sp)] = dict(row)
    return out
