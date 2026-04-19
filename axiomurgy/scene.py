"""Cosmogony: declarative scene / world-model slice (advisory)."""

from __future__ import annotations

from typing import Any, Dict, List

from .legacy import Spell


def build_scene(spell: Spell) -> Dict[str, Any]:
    """Minimal scene: graph step ids as entities; does not duplicate full spell inputs."""
    entities: List[Dict[str, Any]] = [{"kind": "step", "id": s.step_id, "rune": s.rune} for s in spell.graph]
    return {
        "spell": spell.name,
        "entities": entities,
        "assumptions": [],
    }
