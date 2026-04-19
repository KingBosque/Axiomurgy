"""Teleology: final cause and objectives (advisory; separate from Ouroboros acceptance_contract)."""

from __future__ import annotations

from typing import Any, Dict, List

from .legacy import Spell


def build_telos(spell: Spell) -> Dict[str, Any]:
    """Deterministic telos block using spell metadata only."""
    intent = (spell.intent or "").strip()
    objectives: List[Dict[str, Any]] = []
    return {
        "final_cause": intent[:512] if intent else None,
        "objectives": objectives,
    }
