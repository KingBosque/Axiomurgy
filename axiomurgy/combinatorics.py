"""Lullian combinatorics: discrete search over symbolic wheels (capped; advisory)."""

from __future__ import annotations

from typing import Any, Dict, List

from .legacy import Spell

DEFAULT_CAP_DEPTH = 4
DEFAULT_MAX_COMBINATIONS = 256


def build_combinatorics_search(spell: Spell) -> Dict[str, Any]:
    runes = sorted({s.rune for s in spell.graph})
    wheels: List[Dict[str, Any]] = [{"name": "runes", "symbols": runes[:16]}]
    return {
        "wheels": wheels,
        "cap_depth": DEFAULT_CAP_DEPTH,
        "max_combinations": DEFAULT_MAX_COMBINATIONS,
        "combinations_evaluated": 0,
        "estimator": "lullian_v1",
    }
