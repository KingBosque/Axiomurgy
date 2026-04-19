"""Hegelian dialectic: explicit reasoning episodes (deterministic; LLM belongs in sidecars)."""

from __future__ import annotations

from typing import Any, Dict, List

from .legacy import Spell


def build_dialectic_trace(spell: Spell) -> Dict[str, Any]:
    """Deterministic dialectic shell; episodes are empty until populated by deterministic rules."""
    episodes: List[Dict[str, Any]] = []
    return {
        "episodes": episodes,
        "spell": spell.name,
    }
