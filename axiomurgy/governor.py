"""Freudian tripartite as read-only projection over policy + spell (advisory)."""

from __future__ import annotations

from typing import Any, Dict

from .legacy import ResolvedRunTarget, Spell
from .planning import load_json


def build_governor_view(resolved: ResolvedRunTarget) -> Dict[str, Any]:
    """Project id / superego / ego from spell constraints and policy shape — not a second policy engine."""
    spell = resolved.spell
    policy = load_json(resolved.policy_path)
    constraints = spell.constraints or {}
    requires = list(constraints.get("requires_approval_for", []))
    deny_rules = policy.get("deny", []) if isinstance(policy.get("deny", []), list) else []
    req_ap = policy.get("requires_approval", []) if isinstance(policy.get("requires_approval", []), list) else []
    return {
        "id": {
            "drives": "write_effects_and_spell_intent",
            "requires_approval_for_effects": requires,
        },
        "superego": {
            "deny_rule_count": len(deny_rules),
            "requires_approval_rule_count": len(req_ap),
        },
        "ego": {
            "risk": str(constraints.get("risk", "low")),
            "orchestrates": "existing_compile_plan_order",
        },
    }


def governor_view_spell_only(spell: Spell) -> Dict[str, Any]:
    """Lightweight projection when only the spell is available."""
    constraints = spell.constraints or {}
    return {
        "id": {"requires_approval_for_effects": list(constraints.get("requires_approval_for", []))},
        "superego": {"deny_rule_count": None},
        "ego": {"risk": str(constraints.get("risk", "low"))},
    }
