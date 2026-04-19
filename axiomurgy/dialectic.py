"""Hegelian dialectic: structured episodes from plan + telos + governor (deterministic; no LLM)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .legacy import Spell


def build_dialectic_trace(
    spell: Spell,
    plan_context: Mapping[str, Any],
    telos_view: Mapping[str, Any],
    governor_view: Mapping[str, Any],
) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = list(plan_context.get("steps") or [])
    n = len(steps)
    n_write = sum(1 for s in steps if s.get("effect") == "write")
    n_ext = len(plan_context.get("external_calls") or [])
    pending = sum(
        1 for s in steps if (s.get("policy") or {}).get("requires_approval") and not (s.get("policy") or {}).get("approved")
    )
    fc = str(telos_view.get("final_cause") or spell.intent or spell.name)
    dist = (telos_view.get("distance_to_goal") or {}).get("value")

    thesis = {
        "summary": f"Execute {n} step(s) toward: {fc[:280]}",
        "emphasis": "goal_forward_completion",
        "kind": "derived",
    }

    antithesis = {
        "summary": (
            f"Conservative read: {n_write} write(s), {n_ext} external boundary call(s), "
            f"{pending} approval gate(s) not yet granted — elevated blast radius and review load."
        ),
        "emphasis": "risk_and_gates_first",
        "kind": "derived",
    }

    unresolved: List[str] = []
    if pending:
        unresolved.append(f"{pending} step(s) require approval before side effects")
    if n_ext:
        unresolved.append("external calls depend on network/runtime availability")
    if n_write:
        unresolved.append("filesystem or stdout writes may be irreversible without rollback coverage")

    synthesis = {
        "summary": (
            "The compiled plan is acceptable under static policy if approvals are granted where required; "
            "distance_to_goal is advisory only."
        ),
        "unresolved": unresolved,
        "kind": "derived",
    }

    tensions: List[Dict[str, Any]] = []
    for t in governor_view.get("tradeoffs") or []:
        if isinstance(t, dict):
            tensions.append(
                {
                    "a": t.get("axis_a"),
                    "b": t.get("axis_b"),
                    "note": t.get("resolution"),
                }
            )
    if not tensions:
        tensions.append({"a": "intent", "b": "constraints", "note": "static_policy_balances_effects"})

    selection_basis = [
        f"Spell risk tier: {spell.constraints.get('risk', 'low')}",
        f"Governor drives: {len(governor_view.get('drives') or [])}",
        f"Governor constraints: {len(governor_view.get('constraints') or [])}",
        f"Telos distance_to_goal.value: {dist}",
        f"Plan steps: {n}",
    ]

    episode = {
        "thesis": thesis,
        "antithesis": antithesis,
        "synthesis": synthesis,
        "tensions": tensions,
        "selection_basis": selection_basis,
    }

    return {
        "kind": "derived",
        "episodes": [episode],
    }
