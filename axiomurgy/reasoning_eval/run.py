"""Orchestrate corpus × mode evaluation runs (plan-only; isolated artifact dirs)."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from axiomurgy.planning import build_plan_summary, resolve_run_target

from .capture import extract_record_from_plan, merge_capture_error
from .modes import apply_eval_mode, mode_flags_snapshot


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "_", s)
    return s.strip("_")[:120] or "spell"


def run_evaluation(
    *,
    corpus_entries: Sequence[Mapping[str, Any]],
    modes: Sequence[str],
    artifact_root: Optional[Path] = None,
    limit: Optional[int] = None,
    include_raw_plan: bool = False,
) -> Dict[str, Any]:
    """
    For each mode, for each corpus spell: apply env, build plan in a fresh artifact subdir, capture record.

    Does not execute spells, mutate spell files, or call Vermyth (``build_plan_summary`` defaults).
    """
    entries = list(corpus_entries)
    if limit is not None:
        entries = entries[: max(0, int(limit))]

    base = artifact_root
    if base is None:
        base = Path(tempfile.mkdtemp(prefix="axiomurgy_reasoning_eval_"))
    else:
        base.mkdir(parents=True, exist_ok=True)

    by_mode: Dict[str, List[Dict[str, Any]]] = {}
    for mode in modes:
        recs: List[Dict[str, Any]] = []
        for ent in entries:
            path = Path(ent.get("_resolved_path") or "")
            if not path.is_file():
                recs.append(
                    merge_capture_error(
                        mode=mode,
                        spell_path=path,
                        corpus_entry=ent,
                        exc=FileNotFoundError(path),
                    )
                )
                continue
            adir = base / f"{_slug(mode)}__{_slug(path.name)}"
            adir.mkdir(parents=True, exist_ok=True)
            with apply_eval_mode(mode):
                try:
                    resolved = resolve_run_target(path, None, None, adir)
                    plan = build_plan_summary(resolved)
                    row = extract_record_from_plan(plan, spell_path=path, corpus_entry=ent, mode=mode)
                    if include_raw_plan:
                        row = dict(row)
                        row["raw_plan"] = plan
                    recs.append(row)
                except Exception as exc:  # noqa: BLE001 — harness surface
                    recs.append(merge_capture_error(mode=mode, spell_path=path, corpus_entry=ent, exc=exc))
        by_mode[mode] = recs

    out_modes: List[Dict[str, Any]] = []
    for mode in modes:
        out_modes.append(
            {
                "mode": mode,
                "flags": mode_flags_snapshot(mode),
                "results": by_mode.get(mode, []),
            }
        )

    return {
        "eval_harness_version": "1.0.0",
        "artifact_root": str(base.resolve()),
        "modes": out_modes,
    }
