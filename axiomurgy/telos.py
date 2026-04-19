"""
Teleology shadow scoring (advisory; separate from Ouroboros acceptance_contract).

Heuristic assumptions (deterministic, auditable):
- concern_rings: four fixed rings; each step splits unit mass across rings from effect/rune/approval.
- distance_to_goal: rises with write count, external calls, and unmet approval gates (clamped 0..1).
- step_scores.step_component: 0..1 from ring exposure + reversibility class (bounded).
- Declared telos: optional spell.constraints["telos"] or spell.inputs["telos"] (dict with final_cause / objectives).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from .legacy import Spell

# Fixed ring catalog (Oikeiôsis-style weights are per-step, not duplicated as top-level blocks).
RING_DEFS: Tuple[Tuple[str, str], ...] = (
    ("self_task", "local_graph_and_immediate_computation"),
    ("user_session", "session_scope_approval_and_interaction"),
    ("repo_system", "repository_filesystem_and_artifacts"),
    ("external_world", "network_and_untrusted_boundaries"),
)

RING_IDS = [r[0] for r in RING_DEFS]


def _normalize_ring_weights(raw: Dict[str, float]) -> Dict[str, float]:
    total = sum(max(0.0, v) for v in raw.values())
    if total <= 0:
        return {rid: 1.0 / len(RING_IDS) for rid in RING_IDS}
    return {rid: max(0.0, raw.get(rid, 0.0)) / total for rid in RING_IDS}


def _reversibility_class(effect: str, rune: str, ext_kind: Optional[str]) -> str:
    if ext_kind is not None:
        return "external"
    if effect == "write":
        if rune in {"gate.file_write", "gate.emit", "gate.archive"}:
            return "reversible_write"
        return "reversible_write"
    if effect == "approve":
        return "approval_gate"
    if effect == "read":
        return "read"
    return "read"


def _step_ring_raw(
    effect: str,
    rune: str,
    requires_approval: bool,
    approved: bool,
    ext_kind: Optional[str],
) -> Dict[str, float]:
    """Unnormalized weights — higher means more 'outer' concern."""
    w = {rid: 0.0 for rid in RING_IDS}
    w["self_task"] += 0.35
    if effect == "read" and ext_kind is None:
        w["repo_system"] += 0.45
        w["self_task"] += 0.2
    if effect in {"transform", "verify", "simulate"}:
        w["repo_system"] += 0.3
    if effect == "write":
        w["repo_system"] += 0.4
        w["external_world"] += 0.15
    if ext_kind is not None:
        w["external_world"] += 0.55
        w["repo_system"] += 0.15
    if requires_approval:
        w["user_session"] += 0.35
        if not approved:
            w["user_session"] += 0.25
    if rune.startswith("gate.openapi") or rune.startswith("gate.mcp"):
        w["external_world"] += 0.25
    return w


def _parse_declared_telos(spell: Spell) -> Optional[Dict[str, Any]]:
    c = spell.constraints.get("telos") if isinstance(spell.constraints, dict) else None
    i = spell.inputs.get("telos") if isinstance(spell.inputs, dict) else None
    if isinstance(c, dict) and c:
        return dict(c)
    if isinstance(i, dict) and i:
        return dict(i)
    return None


def _derived_final_cause(spell: Spell) -> str:
    intent = (spell.intent or "").strip()
    if intent:
        return intent[:512]
    n = len(spell.graph)
    return f"Complete spell '{spell.name}' ({n} step(s)) successfully."


def _default_objectives(spell: Spell, final_cause: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": "complete_graph",
            "summary": "Execute all planned steps in dependency order",
            "weight": 1.0,
            "kind": "derived",
        },
        {
            "id": "honor_intent",
            "summary": final_cause[:256],
            "weight": 0.8,
            "kind": "derived",
        },
    ]


def build_telos(spell: Spell, plan_context: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Shadow telos view: scoring against final cause using static plan_context only.
    """
    steps: List[Dict[str, Any]] = list(plan_context.get("steps") or [])
    write_steps = plan_context.get("write_steps") or []
    ext_calls = plan_context.get("external_calls") or []
    req_ap = plan_context.get("required_approvals") or []

    declared = _parse_declared_telos(spell)
    if declared and (declared.get("final_cause") or declared.get("objectives")):
        kind = "declared"
        final_cause = str(declared.get("final_cause") or _derived_final_cause(spell)).strip()[:512]
        if isinstance(declared.get("objectives"), list):
            objectives = []
            for o in declared["objectives"]:
                if isinstance(o, dict):
                    oc = dict(o)
                    oc.setdefault("kind", "declared")
                    objectives.append(oc)
                else:
                    objectives.append(o)
        else:
            objectives = _default_objectives(spell, final_cause)
    else:
        kind = "derived"
        final_cause = _derived_final_cause(spell)
        objectives = _default_objectives(spell, final_cause)

    concern_rings_catalog = [{"id": rid, "label": lab, "weight": 1.0 / len(RING_DEFS)} for rid, lab in RING_DEFS]

    step_scores: List[Dict[str, Any]] = []
    for row in sorted(steps, key=lambda r: (r.get("index", 0), r.get("step_id", ""))):
        sid = row["step_id"]
        effect = str(row.get("effect", "transform"))
        rune = str(row.get("rune", ""))
        pol = row.get("policy") or {}
        req = bool(pol.get("requires_approval"))
        appr = bool(pol.get("approved"))
        # Reconstruct external kind from row (same logic as planning)
        ext_kind = None
        if rune == "gate.openapi_call":
            ext_kind = "openapi"
        elif rune == "gate.mcp_call_tool":
            ext_kind = "mcp"
        raw = _step_ring_raw(effect, rune, req, appr, ext_kind)
        ring_impact = _normalize_ring_weights(raw)
        rev = _reversibility_class(effect, rune, ext_kind)
        # Bounded step score 0..1: outer rings + approval friction
        outer = ring_impact.get("external_world", 0) + ring_impact.get("user_session", 0) * 0.5
        base = min(1.0, outer + (0.15 if req and not appr else 0.0))
        step_scores.append(
            {
                "step_id": sid,
                "index": row.get("index"),
                "ring_impact": ring_impact,
                "reversibility": rev,
                "step_component": round(base, 4),
            }
        )

    unmet = sum(1 for r in req_ap if not r.get("granted"))
    n_write = len(write_steps)
    n_ext = len(ext_calls)
    # distance: higher = farther from ideal "done safely"
    raw_dist = 0.08 * n_write + 0.12 * n_ext + 0.18 * min(unmet, 5) + 0.02 * max(0, len(steps) - 1)
    distance = min(1.0, raw_dist)

    return {
        "kind": kind,
        "shadow_mode": True,
        "final_cause": final_cause,
        "objectives": objectives,
        "concern_rings": concern_rings_catalog,
        "distance_to_goal": {
            "value": round(distance, 4),
            "unit": "heuristic",
            "interpretation": "higher means more external/write/approval burden vs ideal local read-only flow",
            "inputs": {
                "write_steps": n_write,
                "external_calls": n_ext,
                "unmet_approval_gates": unmet,
                "step_count": len(steps),
            },
        },
        "step_scores": step_scores,
    }
