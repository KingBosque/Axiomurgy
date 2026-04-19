"""
Friction: bounded heuristic fragility estimates (advisory; deterministic).

Scoring (auditable, 0..1 per step):
- Start from telos shadow `step_scores.step_component` for the same step_id (already ring/approval-aware).
- Add bounded deltas: external boundary (+0.12), write surface (+0.10), unmet approval (+0.08),
  multi-dependency coupling (+0.04 when len(depends_on) > 1).
- If dialectic `synthesis.unresolved` is non-empty, add +0.02 per unresolved item (cap +0.06) on steps
  that already carry external, write, or approval risk — reflects governor/dialectic tension without
  double-counting telos.
- Clamp to [0, 1]. `interpretation` thresholds: low < 0.34, medium < 0.67, else high.

`fallback_absence`: true when the step is external or write and the forward graph step_id is not
listed as `compensates` on any spell rollback step (and there is no rollback at all → true for risky steps).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set

from .legacy import Spell


def _interpret(v: float) -> str:
    if v < 0.34:
        return "low"
    if v < 0.67:
        return "medium"
    return "high"


def _compensated_forward_ids(spell: Spell) -> Set[str]:
    out: Set[str] = set()
    for rb in spell.rollback:
        if rb.compensates:
            out.add(str(rb.compensates))
    return out


def _dialectic_unresolved_n(dialectic_view: Mapping[str, Any]) -> int:
    eps = dialectic_view.get("episodes") or []
    if not eps:
        return 0
    syn = (eps[0] or {}).get("synthesis") or {}
    u = syn.get("unresolved") or []
    return len(u) if isinstance(u, list) else 0


def _risk_factors_and_notes(
    row: Mapping[str, Any],
) -> tuple[List[str], List[str]]:
    rune = str(row.get("rune", ""))
    effect = str(row.get("effect", ""))
    pol = row.get("policy") or {}
    ext = rune.startswith("gate.openapi") or rune.startswith("gate.mcp")
    factors: Set[str] = set()
    notes: List[str] = []
    if ext:
        factors.add("external_dependency")
        notes.append("External boundary may stall on network, credentials, or remote availability.")
    if effect == "write":
        factors.add("irreversible_write")
        notes.append("Write surface may be hard to undo without rollback coverage.")
    if bool(pol.get("requires_approval")) and not bool(pol.get("approved")):
        factors.add("approval_bottleneck")
        notes.append("Approval gate blocks forward progress until granted.")
    if effect == "approve":
        factors.add("human_review_gate")
        notes.append("Explicit human review step may block or serialize execution.")
    deps = row.get("depends_on") or []
    if isinstance(deps, list) and len(deps) > 1:
        factors.add("high_coupling")
        notes.append("Multiple upstream dependencies increase ordering and failure coupling.")
    if ext or effect == "write":
        factors.add("coordination_dependency")
    return sorted(factors), notes[:3]


def build_friction(
    spell: Spell,
    plan_context: Mapping[str, Any],
    telos_view: Mapping[str, Any],
    governor_view: Mapping[str, Any],
    dialectic_view: Mapping[str, Any],
) -> Dict[str, Any]:
    del governor_view  # reserved: tradeoffs already folded into telos/dialectic signals used here
    steps = sorted(
        list(plan_context.get("steps") or []),
        key=lambda r: (int(r.get("index", 0)), str(r.get("step_id", ""))),
    )
    telos_by_step: Dict[str, float] = {}
    for row in telos_view.get("step_scores") or []:
        if isinstance(row, dict) and row.get("step_id") is not None:
            try:
                telos_by_step[str(row["step_id"])] = float(row.get("step_component", 0.0))
            except (TypeError, ValueError):
                telos_by_step[str(row["step_id"])] = 0.0

    compensated = _compensated_forward_ids(spell)
    has_any_rollback = len(spell.rollback) > 0
    unresolved_n = _dialectic_unresolved_n(dialectic_view)
    dialectic_bump_cap = min(0.06, 0.02 * float(unresolved_n))

    per_step: List[Dict[str, Any]] = []
    values: List[float] = []

    for row in steps:
        sid = str(row["step_id"])
        ix = int(row.get("index", 0)) - 1
        rune = str(row.get("rune", ""))
        effect = str(row.get("effect", ""))
        pol = row.get("policy") or {}
        ext = rune.startswith("gate.openapi") or rune.startswith("gate.mcp")
        base = float(telos_by_step.get(sid, 0.0))
        v = base
        if ext:
            v += 0.12
        if effect == "write":
            v += 0.10
        if bool(pol.get("requires_approval")) and not bool(pol.get("approved")):
            v += 0.08
        deps = row.get("depends_on") or []
        if isinstance(deps, list) and len(deps) > 1:
            v += 0.04
        risky = ext or effect == "write" or (bool(pol.get("requires_approval")) and not bool(pol.get("approved")))
        if risky and dialectic_bump_cap > 0:
            v += dialectic_bump_cap
        v = max(0.0, min(1.0, round(v, 4)))
        values.append(v)

        factors, note_list = _risk_factors_and_notes(row)
        is_ext_or_write = ext or effect == "write"
        if is_ext_or_write:
            if not has_any_rollback:
                fb_abs = True
            else:
                fb_abs = sid not in compensated
        else:
            fb_abs = False

        per_step.append(
            {
                "step_id": sid,
                "index": max(0, ix),
                "value": v,
                "interpretation": _interpret(v),
                "risk_factors": factors,
                "fallback_absence": fb_abs,
                "contingency_notes": note_list,
            }
        )

    overall_v = 0.0 if not values else round(sum(values) / len(values), 4)
    ranked = sorted(
        range(len(per_step)),
        key=lambda i: (-float(per_step[i]["value"]), str(per_step[i]["step_id"])),
    )
    bottlenecks: List[Dict[str, str]] = []
    for i in ranked[:3]:
        ps = per_step[i]
        reasons = ", ".join(ps["risk_factors"]) if ps["risk_factors"] else "elevated_heuristic_friction"
        bottlenecks.append({"step_id": str(ps["step_id"]), "reason": reasons})

    return {
        "kind": "derived",
        "overall_friction": {
            "value": overall_v,
            "unit": "heuristic_0_1",
            "interpretation": _interpret(overall_v),
        },
        "per_step_friction": per_step,
        "bottlenecks": bottlenecks,
    }


def estimate_friction(spell: Spell) -> Dict[str, Any]:
    """Backward-compatible stub when plan/telos context is unavailable."""
    plan_context: Dict[str, Any] = {"steps": []}
    telos_view = {"step_scores": []}
    governor_view: Dict[str, Any] = {}
    dialectic_view: Dict[str, Any] = {"episodes": [{"synthesis": {"unresolved": []}}]}
    return build_friction(spell, plan_context, telos_view, governor_view, dialectic_view)
