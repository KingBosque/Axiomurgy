"""Clausewitz friction: epistemic uncertainty estimates (advisory)."""

from __future__ import annotations

from typing import Any, Dict

from .legacy import Spell


def estimate_friction(spell: Spell) -> Dict[str, Any]:
    write_count = sum(1 for s in spell.graph if s.effect == "write")
    ext = sum(1 for s in spell.graph if s.rune in {"gate.openapi_call", "gate.mcp_call_tool"})
    return {
        "write_step_count": write_count,
        "external_call_count": ext,
        "contingency_score": round(0.05 * write_count + 0.03 * ext, 4),
        "estimator": "clausewitz_v1",
    }
