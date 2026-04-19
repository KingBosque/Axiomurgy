"""Correspondence: structural levels / lifting hints (advisory; does not reorder compile_plan)."""

from __future__ import annotations

from typing import Any, Dict, List

from .legacy import Spell


def build_correspondence(spell: Spell) -> Dict[str, Any]:
    """Single-level map of all steps; deterministic placeholder for richer fractal rules."""
    step_ids = [s.step_id for s in spell.graph]
    levels: List[Dict[str, Any]] = [{"level": 0, "step_ids": step_ids, "note": "flat_default"}]
    return {
        "levels": levels,
        "rules_applied": [],
    }
