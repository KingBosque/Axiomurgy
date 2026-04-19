"""
Governor as deterministic tradeoff projection over spell + policy + static plan rows.

Not a second policy engine: uses only evaluate_policy_static outcomes already on plan_context steps.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .legacy import ResolvedRunTarget, Spell
from .planning import load_json


def build_governor_view(resolved: ResolvedRunTarget, plan_context: Mapping[str, Any]) -> Dict[str, Any]:
    spell = resolved.spell
    policy = load_json(resolved.policy_path)
    constraints = spell.constraints or {}
    steps: List[Dict[str, Any]] = list(plan_context.get("steps") or [])
    risk = str(constraints.get("risk", "low"))
    deny_n = len(policy.get("deny", [])) if isinstance(policy.get("deny", []), list) else 0
    req_ap_rules = len(policy.get("requires_approval", [])) if isinstance(policy.get("requires_approval", []), list) else 0

    n_write = sum(1 for s in steps if s.get("effect") == "write")
    n_ext = sum(1 for s in steps if s.get("rune", "").startswith("gate.openapi") or s.get("rune", "").startswith("gate.mcp"))
    pending_ap = sum(
        1 for s in steps if (s.get("policy") or {}).get("requires_approval") and not (s.get("policy") or {}).get("approved")
    )

    drives: List[Dict[str, Any]] = [
        {
            "id": "complete_plan",
            "summary": "Execute all steps to satisfy spell intent",
            "kind": "derived",
        },
        {
            "id": "minimize_blast_radius",
            "summary": "Prefer local reads; isolate writes and external calls",
            "kind": "derived",
        },
    ]
    if n_write or n_ext:
        drives.append(
            {
                "id": "deliver_side_effects",
                "summary": f"Carry {n_write} write(s) and {n_ext} external call boundary(s)",
                "kind": "derived",
            }
        )

    constraints_out: List[Dict[str, Any]] = [
        {
            "id": "policy_deny_rules",
            "summary": f"{deny_n} deny rule(s) in policy",
            "kind": "derived",
        },
        {
            "id": "policy_approval_rules",
            "summary": f"{req_ap_rules} requires_approval rule(s) in policy",
            "kind": "derived",
        },
        {
            "id": "spell_risk",
            "summary": f"Spell risk tier: {risk}",
            "kind": "derived",
        },
        {
            "id": "spell_requires_approval_for",
            "summary": f"Effects requiring approval: {list(constraints.get('requires_approval_for', []))}",
            "kind": "derived",
        },
    ]
    if pending_ap:
        constraints_out.append(
            {
                "id": "pending_approval_gates",
                "summary": f"{pending_ap} step(s) still require approval grant",
                "kind": "derived",
            }
        )

    mediator = {
        "summary": "Balance intent completion against policy gates and risk tier using existing static plan decisions.",
        "strategy": "sequential_execution_with_pre_approval_where_required",
    }

    tradeoffs: List[Dict[str, Any]] = []
    if n_write and n_ext:
        tradeoffs.append(
            {
                "axis_a": "speed",
                "axis_b": "reversibility",
                "resolution": "writes_and_external_calls_increase_irreversible_surface",
            }
        )
    if pending_ap:
        tradeoffs.append(
            {
                "axis_a": "completeness",
                "axis_b": "approval_burden",
                "resolution": "pending_approval_blocks_unapproved_writes",
            }
        )
    if risk in {"high", "critical"} and n_write:
        tradeoffs.append(
            {
                "axis_a": "throughput",
                "axis_b": "safety",
                "resolution": "high_risk_spell_with_writes_requires_explicit_approval_discipline",
            }
        )
    if not tradeoffs:
        tradeoffs.append(
            {
                "axis_a": "caution",
                "axis_b": "progress",
                "resolution": "low_friction_plan_static_policy_allows_forward_progress",
            }
        )

    return {
        "kind": "derived",
        "drives": drives,
        "constraints": constraints_out,
        "mediator": mediator,
        "tradeoffs": tradeoffs,
    }


def governor_view_spell_only(spell: Spell) -> Dict[str, Any]:
    """Lightweight projection when only the spell is available."""
    constraints = spell.constraints or {}
    return {
        "kind": "derived",
        "drives": [{"id": "intent", "summary": spell.intent[:200] if spell.intent else "", "kind": "derived"}],
        "constraints": [
            {"id": "spell_risk", "summary": str(constraints.get("risk", "low")), "kind": "derived"},
        ],
        "mediator": {"summary": "insufficient context for tradeoffs", "strategy": "none"},
        "tradeoffs": [],
    }
