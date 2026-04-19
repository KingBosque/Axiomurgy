"""
Parthenogenesis v1: bounded, review-bound candidate offspring (advisory only).

Does not emit spell JSON, does not write files, does not execute. Candidates are deterministic
derivations from telos, governor, dialectic, correspondence, friction, and optional Wyrd hints.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .legacy import ResolvedRunTarget

DEFAULT_CANDIDATE_CAP = 3

CANDIDATE_KINDS = (
    "subgoal_split",
    "risk_reduction_variant",
    "approval_first_variant",
    "boundary_isolation_variant",
)


def reasoning_generation_enabled() -> bool:
    v = os.environ.get("AXIOMURGY_REASONING_GENERATION", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _canonical_candidate_id(spell_name: str, kind: str, key: str) -> str:
    h = hashlib.sha256(f"{spell_name}|{kind}|{key}".encode("utf-8")).hexdigest()[:24]
    return f"cand_{h}"


def _objective_ids(telos: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for o in telos.get("objectives") or []:
        if isinstance(o, dict) and o.get("id") is not None:
            out.append(str(o["id"]))
    return sorted(set(out)) or ["complete_graph"]


def _step_rows(plan_context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    steps = list(plan_context.get("steps") or [])
    return sorted(steps, key=lambda r: (int(r.get("index", 0)), str(r.get("step_id", ""))))


def _is_external_row(row: Mapping[str, Any]) -> bool:
    r = str(row.get("rune", ""))
    return r.startswith("gate.openapi") or r.startswith("gate.mcp")


def _dialectic_tension_notes(reasoning: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for ep in (reasoning.get("dialectic") or {}).get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        for t in ep.get("tensions") or []:
            if isinstance(t, dict) and t.get("note"):
                out.append(str(t["note"])[:512])
    return out[:8]


def _wyrd_support_ids(wyrd_hints: Mapping[str, Any], kinds_pref: Sequence[str]) -> List[str]:
    ids: List[str] = []
    pref = set(kinds_pref)
    for n in wyrd_hints.get("recent_nodes") or []:
        if not isinstance(n, dict):
            continue
        k = str(n.get("kind", ""))
        if k in pref or "bottleneck" in str(n.get("summary", "")).lower():
            nid = n.get("node_id")
            if nid:
                ids.append(str(nid))
    return sorted(set(ids))[:6]


def build_parthenogenesis_candidates(
    resolved: ResolvedRunTarget,
    plan_context: Mapping[str, Any],
    reasoning: Mapping[str, Any],
    *,
    wyrd_hints: Mapping[str, Any],
    run_id: str = "",
    plan_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build up to DEFAULT_CANDIDATE_CAP candidates. Empty list when no rule fires.
    Full generation runs for `--plan` only (plan_summary not None); `--describe` returns an honest empty list.
    """
    if not reasoning_generation_enabled():
        return {
            "kind": "derived",
            "bounded": True,
            "review_required": True,
            "candidates": [],
            "generation_enabled": False,
        }

    if plan_summary is None:
        return {
            "kind": "derived",
            "bounded": True,
            "review_required": True,
            "candidates": [],
            "generation_enabled": True,
            "notes": ["plan_path_preferred_for_generation"],
        }

    spell = resolved.spell
    spell_name = spell.name
    steps = _step_rows(plan_context)
    n = len(steps)
    if n == 0:
        return {
            "kind": "derived",
            "bounded": True,
            "review_required": True,
            "candidates": [],
            "generation_enabled": True,
            "notes": ["insufficient_plan_context"],
        }

    telos = reasoning.get("telos") or {}
    obj_ids = _objective_ids(telos)
    exp = reasoning.get("experimental") or {}
    fr = exp.get("friction") or {}
    co = exp.get("correspondence") or {}
    clusters = [c for c in (co.get("clusters") or []) if isinstance(c, dict)]
    per_sf = [p for p in (fr.get("per_step_friction") or []) if isinstance(p, dict)]
    bottlenecks = [b for b in (fr.get("bottlenecks") or []) if isinstance(b, dict)]
    tension_notes = _dialectic_tension_notes(reasoning)

    by_step = {str(p.get("step_id")): p for p in per_sf if p.get("step_id")}

    def parent_refs(step_ids: List[str]) -> Dict[str, Any]:
        return {
            "spell_name": spell_name,
            "run_id": run_id or "",
            "plan_step_ids": step_ids,
        }

    def base_supported(cluster_ids: List[str], bneck_step_ids: List[str]) -> Dict[str, Any]:
        wids = _wyrd_support_ids(wyrd_hints, ("friction_bottleneck", "telos", "dialectic_episode"))
        return {
            "friction_bottlenecks": bneck_step_ids[:6],
            "correspondence_cluster_ids": cluster_ids[:8],
            "wyrd_node_ids": wids,
            "dialectic_tensions": tension_notes[:6],
        }

    ordered: List[Dict[str, Any]] = []

    # 1) risk_reduction_variant
    risky_steps: List[str] = []
    for p in per_sf:
        fac = set(p.get("risk_factors") or [])
        if "irreversible_write" in fac or "external_dependency" in fac:
            sid = str(p.get("step_id", ""))
            if sid:
                risky_steps.append(sid)
    risky_steps = sorted(set(risky_steps))
    if risky_steps and len(ordered) < DEFAULT_CANDIDATE_CAP:
        sid0 = risky_steps[0]
        others = [str(s["step_id"]) for s in steps if str(s["step_id"]) != sid0]
        cids = [str(c.get("cluster_id")) for c in clusters if sid0 in (c.get("step_ids") or [])]
        ordered.append(
            {
                "candidate_id": _canonical_candidate_id(spell_name, "risk_reduction_variant", sid0),
                "candidate_kind": "risk_reduction_variant",
                "parent_refs": parent_refs([sid0]),
                "generation_reason": (
                    "Friction marks irreversible write or external dependency on this step; "
                    "stage non-risky work first and defer the risky step behind explicit review."
                ),
                "target_telos_objective_ids": obj_ids[:4],
                "supported_by": base_supported(cids, [sid0]),
                "expected_benefit": [
                    "Smaller blast radius before irreversible or external work.",
                    "Clearer rollback/review focus on the deferred step.",
                ],
                "expected_cost": ["Extra staging may add latency.", "May duplicate local reads/transforms."],
                "friction_delta": {
                    "direction": "reduce",
                    "summary": "Aims to lower combined external/write friction before the deferred commit point.",
                },
                "boundary_effect": "isolates",
                "approval_effect": "not_applicable",
                "execution_ready": False,
                "review_required": True,
                "proposed_structure": {
                    "summary": "Preflight with non-risky steps; commit the flagged risky step only after review.",
                    "candidate_steps": [
                        {"group": "preflight", "step_ids": others, "note": "non_risky_prefix"},
                        {"group": "commit", "step_ids": [sid0], "note": "deferred_risky_step"},
                    ],
                },
            }
        )

    # 2) boundary_isolation_variant
    ext_indices = [i for i, s in enumerate(steps) if _is_external_row(s)]
    local_indices = [i for i, s in enumerate(steps) if not _is_external_row(s)]
    if (
        ext_indices
        and local_indices
        and min(ext_indices) < max(local_indices)
        and len(ordered) < DEFAULT_CANDIDATE_CAP
    ):
        ext_steps = [str(steps[i]["step_id"]) for i in ext_indices]
        loc_steps = [str(steps[i]["step_id"]) for i in local_indices]
        key = "|".join(ext_steps + loc_steps[:4])
        cids = [str(c.get("cluster_id")) for c in clusters if c.get("motif") == "external_boundary"]
        bn_ids = [str(b.get("step_id")) for b in bottlenecks if b.get("step_id")]
        ordered.append(
            {
                "candidate_id": _canonical_candidate_id(spell_name, "boundary_isolation_variant", key),
                "candidate_kind": "boundary_isolation_variant",
                "parent_refs": parent_refs(list(dict.fromkeys(loc_steps + ext_steps))),
                "generation_reason": (
                    "External-boundary steps are interleaved with local steps; batch boundaries "
                    "to narrow failure domains."
                ),
                "target_telos_objective_ids": obj_ids[:4],
                "supported_by": base_supported(cids, bn_ids[:3]),
                "expected_benefit": [
                    "Clearer separation between repo-local work and network I/O.",
                    "Simpler policy/capability review per boundary phase.",
                ],
                "expected_cost": ["Reordering may require re-checking dataflow references."],
                "friction_delta": {
                    "direction": "mixed",
                    "summary": "May reduce cross-boundary coupling; approval timing may shift.",
                },
                "boundary_effect": "isolates",
                "approval_effect": "unchanged",
                "execution_ready": False,
                "review_required": True,
                "proposed_structure": {
                    "summary": "Run local phases and external phases as contiguous blocks instead of interleaving.",
                    "candidate_steps": [
                        {"group": "local_phase", "step_ids": loc_steps, "note": "non_external_runes"},
                        {"group": "boundary_phase", "step_ids": ext_steps, "note": "openapi_or_mcp"},
                    ],
                },
            }
        )

    # 3) approval_first_variant
    approve_idx: List[int] = []
    for i, s in enumerate(steps):
        if str(s.get("effect")) == "approve":
            approve_idx.append(i)
    late_approve = False
    if approve_idx and n >= 4:
        ai = approve_idx[0]
        thr = max(0, (2 * n + 2) // 3 - 1)
        late_approve = ai >= thr
    appr_bneck = False
    for aid in approve_idx:
        sid = str(steps[aid]["step_id"])
        p = by_step.get(sid, {})
        fac = set(p.get("risk_factors") or [])
        if "human_review_gate" in fac or p.get("interpretation") in ("medium", "high"):
            appr_bneck = True
    writes_after = False
    if approve_idx:
        a0 = approve_idx[0]
        for j in range(a0 + 1, n):
            if str(steps[j].get("effect")) == "write":
                writes_after = True
                break
    if (
        approve_idx
        and (late_approve or appr_bneck)
        and writes_after
        and len(ordered) < DEFAULT_CANDIDATE_CAP
    ):
        sid_ap = str(steps[approve_idx[0]]["step_id"])
        cids = [str(c.get("cluster_id")) for c in clusters if c.get("motif") == "approval_gate"]
        ordered.append(
            {
                "candidate_id": _canonical_candidate_id(spell_name, "approval_first_variant", sid_ap),
                "candidate_kind": "approval_first_variant",
                "parent_refs": parent_refs([sid_ap]),
                "generation_reason": (
                    "Human approval gate is late in the graph or high-friction, with writes after it; "
                    "consider an earlier review checkpoint before downstream writes."
                ),
                "target_telos_objective_ids": obj_ids[:4],
                "supported_by": base_supported(cids, [sid_ap]),
                "expected_benefit": [
                    "Earlier human gate before expensive or irreversible downstream writes.",
                ],
                "expected_cost": ["May require draft material earlier; possible extra human round trips."],
                "friction_delta": {
                    "direction": "mixed",
                    "summary": "May reduce late approval bottlenecks by shifting the gate earlier.",
                },
                "boundary_effect": "reorders",
                "approval_effect": "earlier",
                "execution_ready": False,
                "review_required": True,
                "proposed_structure": {
                    "summary": "Move or duplicate review so human approval precedes the first major write after the gate.",
                    "candidate_steps": [
                        {"group": "early_review", "step_ids": [sid_ap], "note": "human_gate"},
                        {
                            "group": "downstream_writes",
                            "step_ids": [str(s["step_id"]) for s in steps if str(s.get("effect")) == "write"],
                            "note": "writes_after_gate",
                        },
                    ],
                },
            }
        )

    # 4) subgoal_split
    if len(clusters) >= 3 and len(ordered) < DEFAULT_CANDIDATE_CAP:
        cids = [str(c.get("cluster_id")) for c in clusters if c.get("cluster_id")]
        key = "|".join(cids)
        cand_steps = [
            {
                "group": f"subgoal_{c.get('cluster_id')}",
                "step_ids": list(c.get("step_ids") or []),
                "note": str(c.get("motif", "")),
            }
            for c in clusters[:8]
        ]
        bn_ids = [str(b.get("step_id")) for b in bottlenecks if b.get("step_id")]
        ordered.append(
            {
                "candidate_id": _canonical_candidate_id(spell_name, "subgoal_split", key),
                "candidate_kind": "subgoal_split",
                "parent_refs": parent_refs([str(s["step_id"]) for s in steps]),
                "generation_reason": (
                    "Correspondence reports multiple objective-linked clusters; split into reviewable subgoals."
                ),
                "target_telos_objective_ids": obj_ids[:4],
                "supported_by": base_supported(cids, bn_ids[:3]),
                "expected_benefit": [
                    "Clearer checkpoints per cluster.",
                    "Parallelizable human review of batches.",
                ],
                "expected_cost": ["More review artifacts if realized; merge discipline required."],
                "friction_delta": {"direction": "unknown", "summary": "Depends on scheduling of subgoals vs monolithic plan."},
                "boundary_effect": "reorders",
                "approval_effect": "unchanged",
                "execution_ready": False,
                "review_required": True,
                "proposed_structure": {
                    "summary": "Execute and review cluster-by-cluster instead of one opaque linear sweep.",
                    "candidate_steps": cand_steps,
                },
            }
        )

    candidates = ordered[:DEFAULT_CANDIDATE_CAP]

    return {
        "kind": "derived",
        "bounded": True,
        "review_required": True,
        "candidates": candidates,
        "generation_enabled": True,
        "cap": DEFAULT_CANDIDATE_CAP,
    }


def build_generation_candidates(spell: Any) -> Dict[str, Any]:
    """Stub when full reasoning graph is not yet available."""
    del spell
    return {
        "kind": "derived",
        "bounded": True,
        "review_required": True,
        "candidates": [],
        "generation_enabled": reasoning_generation_enabled(),
        "notes": ["use_build_parthenogenesis_candidates"],
    }
