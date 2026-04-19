"""Parthenogenesis: controlled generation candidates (advisory; review before emit)."""

from __future__ import annotations

from typing import Any, Dict, List

from .legacy import Spell


def build_generation_candidates(spell: Spell) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    return {
        "candidates": candidates,
        "cap": 8,
        "spell": spell.name,
        "status": "empty",
    }
