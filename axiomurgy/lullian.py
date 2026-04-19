"""
Lullian v1: bounded symbolic verification and ranking over Parthenogenesis candidates.

Not a combinatorial search engine and not a planner: it only compares the base compiled plan
against already-generated candidates using a fixed dimension wheel. Deterministic; advisory only.

Ranking policy (lexicographic, stable):
1. Fewer dimension outcomes tagged ``contradicts`` (ascending).
2. More ``improves`` (descending).
3. Fewer ``regresses`` (ascending).
4. ``candidate_id`` (ascending) as final tie-break.

``verification_status`` per candidate (derived from counts, not prose):
- ``rejected``: any ``contradicts`` on any dimension.
- ``preferred``: ``contradicts`` == 0 and ``improves`` >= 3.
- ``acceptable``: ``contradicts`` == 0 and 1 <= ``improves`` < 3.
- ``mixed``: ``contradicts`` == 0, ``improves`` == 0, ``regresses`` >= 1, or otherwise not meeting the above.
- ``insufficient_evidence``: ``contradicts`` == 0, ``improves`` == 0, ``regresses`` == 0, and ``unknown`` >= 5.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .legacy import ResolvedRunTarget

DIMENSION_ORDER: Tuple[str, ...] = (
    "telos_coverage",
    "concern_ring_impact",
    "friction_reduction",
    "boundary_isolation",
    "approval_positioning",
    "reversibility",
    "correspondence_preservation",
    "wyrd_consistency",
)

STATUS_SET = frozenset({"improves", "preserves", "regresses", "unknown", "contradicts"})


def reasoning_lullian_enabled() -> bool:
    v = os.environ.get("AXIOMURGY_REASONING_LULLIAN", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _step_rows(plan_context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    steps = list(plan_context.get("steps") or [])
    return sorted(steps, key=lambda r: (int(r.get("index", 0)), str(r.get("step_id", ""))))


def _is_external_row(row: Mapping[str, Any]) -> bool:
    r = str(row.get("rune", ""))
    return r.startswith("gate.openapi") or r.startswith("gate.mcp")


def _interleaved_external(steps: Sequence[Mapping[str, Any]]) -> bool:
    ext_i = [i for i, s in enumerate(steps) if _is_external_row(s)]
    loc_i = [i for i, s in enumerate(steps) if not _is_external_row(s)]
    return bool(ext_i and loc_i and min(ext_i) < max(loc_i))


def _avg_outer_ring(telos: Mapping[str, Any]) -> Optional[float]:
    scores = telos.get("step_scores") or []
    if not scores:
        return None
    vals: List[float] = []
    for row in scores:
        if not isinstance(row, dict):
            continue
        ri = row.get("ring_impact") or {}
        if isinstance(ri, dict):
            vals.append(float(ri.get("external_world", 0.0)) + float(ri.get("user_session", 0.0)))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _wyrd_unknown(wyrd_hints: Mapping[str, Any]) -> bool:
    notes = wyrd_hints.get("consistency_notes") or []
    if wyrd_hints.get("recent_nodes") == [] and "no_wyrd_database" in notes:
        return True
    if "wyrd_disabled" in notes:
        return True
    if "no_prior_matching_context" in notes:
        return True
    return False


def _wyrd_preserves_hint(wyrd_hints: Mapping[str, Any]) -> bool:
    if _wyrd_unknown(wyrd_hints):
        return False
    if (wyrd_hints.get("related_prior_runs") or []) != []:
        return True
    for n in wyrd_hints.get("recent_nodes") or []:
        if isinstance(n, dict) and n.get("kind") in ("telos", "friction_bottleneck", "dialectic_episode"):
            return True
    return False


def _dim(
    dimension: str,
    status: str,
    evidence: List[str],
    summary: str,
) -> Dict[str, Any]:
    if status not in STATUS_SET:
        status = "unknown"
    return {"dimension": dimension, "status": status, "evidence": evidence[:12], "summary": summary[:512]}


def _baseline_dimensions(
    reasoning: Mapping[str, Any],
    plan_context: Mapping[str, Any],
    wyrd_hints: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    telos = reasoning.get("telos") or {}
    dist = (telos.get("distance_to_goal") or {}).get("value")
    n_obj = len([o for o in (telos.get("objectives") or []) if isinstance(o, dict)])
    avg_outer = _avg_outer_ring(telos)
    fr = (reasoning.get("experimental") or {}).get("friction") or {}
    overall = (fr.get("overall_friction") or {}).get("value")
    steps = _step_rows(plan_context)
    inter = _interleaved_external(steps)

    out: List[Dict[str, Any]] = []
    out.append(
        _dim(
            "telos_coverage",
            "preserves",
            [f"objectives_count:{n_obj}", f"distance_to_goal:{dist}"],
            "Baseline plan aligns with current telos objectives and distance heuristic.",
        )
    )
    ring_note = f"avg_outer_ring_proxy:{avg_outer}" if avg_outer is not None else "no_step_scores"
    out.append(
        _dim(
            "concern_ring_impact",
            "preserves" if avg_outer is None or avg_outer <= 0.55 else "unknown",
            [ring_note, f"interleaved_external:{inter}"],
            "Current compiled plan ring exposure snapshot (advisory).",
        )
    )
    out.append(
        _dim(
            "friction_reduction",
            "preserves",
            [f"overall_friction:{overall}"],
            "Baseline aggregate friction from shadow scoring.",
        )
    )
    out.append(
        _dim(
            "boundary_isolation",
            "preserves",
            [f"interleaved_external:{inter}"],
            "Whether external calls are interleaved with local steps in the base plan.",
        )
    )
    out.append(_dim("approval_positioning", "preserves", ["compiled_order"], "Approval/write order as in static plan rows."))
    out.append(_dim("reversibility", "preserves", ["rollback_spell_config"], "Reversibility class from telos per-step rows."))
    out.append(
        _dim(
            "correspondence_preservation",
            "preserves",
            [f"clusters:{len((reasoning.get('experimental') or {}).get('correspondence', {}).get('clusters') or [])}"],
            "Cluster structure from correspondence (baseline).",
        )
    )
    ws = "unknown" if _wyrd_unknown(wyrd_hints) else ("preserves" if _wyrd_preserves_hint(wyrd_hints) else "unknown")
    out.append(
        _dim(
            "wyrd_consistency",
            ws,
            list(wyrd_hints.get("consistency_notes") or [])[:4],
            "Wyrd hint availability for this artifact dir (optional memory).",
        )
    )
    return out


def _overlap_preflight_commit(candidate: Mapping[str, Any]) -> bool:
    ps = candidate.get("proposed_structure") or {}
    groups = ps.get("candidate_steps") or []
    pre: List[str] = []
    commit: List[str] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        note = str(g.get("note", "")).lower()
        ids = [str(x) for x in (g.get("step_ids") or []) if x is not None]
        if "preflight" in note or "local_phase" in note or "non_risky" in note:
            pre.extend(ids)
        if "commit" in note or "boundary_phase" in note or "deferred" in note:
            commit.extend(ids)
    if not pre or not commit:
        return False
    return bool(set(pre) & set(commit))


def _evaluate_candidate(
    cand: Mapping[str, Any],
    *,
    reasoning: Mapping[str, Any],
    plan_context: Mapping[str, Any],
    wyrd_hints: Mapping[str, Any],
    interleaved_base: bool,
) -> Tuple[List[Dict[str, Any]], List[str], List[str], List[str], Dict[str, int]]:
    kind = str(cand.get("candidate_kind", ""))

    telos_st = "preserves"
    ring_st = "preserves"
    fr_st = "preserves"
    bound_st = "preserves"
    appr_st = "preserves"
    rev_st = "preserves"
    co_st = "preserves"
    wy_st: str = "unknown" if _wyrd_unknown(wyrd_hints) else ("preserves" if _wyrd_preserves_hint(wyrd_hints) else "unknown")

    if kind == "risk_reduction_variant":
        fr_st = "improves"
        rev_st = "improves"
        ring_st = "improves"
        if _overlap_preflight_commit(cand):
            co_st = "contradicts"
            rev_st = "contradicts"

    elif kind == "boundary_isolation_variant":
        if interleaved_base:
            bound_st = "improves"
            ring_st = "improves"
            rev_st = "improves"
        else:
            bound_st = "preserves"
            ring_st = "unknown"

    elif kind == "approval_first_variant":
        appr_st = "improves"
        fr_st = "improves"

    elif kind == "subgoal_split":
        co_st = "improves"
        telos_st = "improves"

    dims = [
        _dim("telos_coverage", telos_st, [f"candidate_kind:{kind}"], "Telos alignment vs baseline."),
        _dim("concern_ring_impact", ring_st, [f"candidate_kind:{kind}"], "Outer ring exposure vs baseline."),
        _dim("friction_reduction", fr_st, [f"candidate_kind:{kind}"], "Friction posture vs baseline."),
        _dim("boundary_isolation", bound_st, [f"candidate_kind:{kind}"], "External boundary batching vs baseline."),
        _dim("approval_positioning", appr_st, [f"candidate_kind:{kind}"], "Approval timing vs baseline."),
        _dim("reversibility", rev_st, [f"candidate_kind:{kind}"], "Irreversible step handling vs baseline."),
        _dim("correspondence_preservation", co_st, [f"candidate_kind:{kind}"], "Cluster/objective coherence vs baseline."),
        _dim("wyrd_consistency", wy_st, [f"candidate_kind:{kind}"], "Optional consistency with Wyrd memory hints."),
    ]

    counts: Dict[str, int] = {"improves": 0, "preserves": 0, "regresses": 0, "contradicts": 0, "unknown": 0}
    for d in dims:
        st = str(d.get("status", "unknown"))
        if st in counts:
            counts[st] += 1

    improvements = [f"{d['dimension']}:{d['summary']}" for d in dims if d.get("status") == "improves"]
    regressions = [f"{d['dimension']}:{d['summary']}" for d in dims if d.get("status") == "regresses"]
    contradictions = [f"{d['dimension']}:{d['summary']}" for d in dims if d.get("status") == "contradicts"]

    return dims, improvements, regressions, contradictions, counts


def _verification_label(counts: Mapping[str, int]) -> str:
    c = int(counts.get("contradicts", 0))
    imp = int(counts.get("improves", 0))
    reg = int(counts.get("regresses", 0))
    unk = int(counts.get("unknown", 0))
    if c >= 1:
        return "rejected"
    if imp >= 3:
        return "preferred"
    if imp >= 1:
        return "acceptable"
    if reg >= 1:
        return "mixed"
    if imp == 0 and c == 0 and unk >= 5:
        return "insufficient_evidence"
    return "mixed"


def build_candidate_verification(
    resolved: ResolvedRunTarget,
    plan_context: Mapping[str, Any],
    reasoning: Mapping[str, Any],
    *,
    plan_summary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    del resolved  # reserved for spellbook/artifact paths in future hooks
    exp = reasoning.get("experimental") or {}
    wyrd_hints = exp.get("wyrd_hints") or {}
    gen = exp.get("generation_candidates") or {}
    candidates = [c for c in (gen.get("candidates") or []) if isinstance(c, dict)]

    if plan_summary is None:
        return {
            "kind": "derived",
            "bounded": True,
            "dimension_order": list(DIMENSION_ORDER),
            "base_plan": {
                "candidate_id": "base_plan",
                "dimension_results": [],
                "verification_status": "baseline",
            },
            "candidate_results": [],
            "selection_note": "Describe path: no symbolic verification against Parthenogenesis candidates (use --plan). Advisory only.",
            "notes": ["plan_path_preferred_for_verification"],
        }

    steps = _step_rows(plan_context)
    interleaved = _interleaved_external(steps)
    base_dims = _baseline_dimensions(reasoning, plan_context, wyrd_hints)

    results_raw: List[Dict[str, Any]] = []
    for cand in candidates:
        cid = str(cand.get("candidate_id", ""))
        kind = str(cand.get("candidate_kind", ""))
        dims, impr, regr, contra, counts = _evaluate_candidate(
            cand, reasoning=reasoning, plan_context=plan_context, wyrd_hints=wyrd_hints, interleaved_base=interleaved
        )
        vstat = _verification_label(counts)
        rationale: List[str] = []
        if counts.get("contradicts", 0):
            rationale.append("contradictions_trigger_rejection_or_mixed")
        elif counts.get("improves", 0) >= 3:
            rationale.append("multiple_dimension_improvements")
        else:
            rationale.append("lexicographic_rank_applies")
        results_raw.append(
            {
                "candidate_id": cid,
                "candidate_kind": kind,
                "dimension_results": dims,
                "improvements": impr,
                "regressions": regr,
                "contradictions": contra,
                "verification_status": vstat,
                "score_summary": {
                    "improves": counts.get("improves", 0),
                    "preserves": counts.get("preserves", 0),
                    "regresses": counts.get("regresses", 0),
                    "contradictions": counts.get("contradicts", 0),
                },
                "rationale": rationale,
                "_sort": (
                    int(counts.get("contradicts", 0)),
                    -int(counts.get("improves", 0)),
                    int(counts.get("regresses", 0)),
                    cid,
                ),
            }
        )

    sorted_c = sorted(results_raw, key=lambda r: r["_sort"])
    ranked: List[Dict[str, Any]] = []
    for idx, row in enumerate(sorted_c):
        row.pop("_sort", None)
        row["rank"] = idx + 1
        if idx + 1 < len(sorted_c):
            nxt = sorted_c[idx + 1]
            cur_key = (
                int(row["score_summary"]["contradictions"]),
                -int(row["score_summary"]["improves"]),
                int(row["score_summary"]["regresses"]),
                str(row["candidate_id"]),
            )
            nxt_key = (
                int(nxt["score_summary"]["contradictions"]),
                -int(nxt["score_summary"]["improves"]),
                int(nxt["score_summary"]["regresses"]),
                str(nxt["candidate_id"]),
            )
            if cur_key == nxt_key:
                row.setdefault("rationale", []).append("tie_broken_lexicographically_by_candidate_id")
        ranked.append(row)

    note = (
        "Lullian v1 ranks candidates symbolically; does not auto-select, execute, or emit spells. "
        "Review is always required for Parthenogenesis candidates."
    )
    out: Dict[str, Any] = {
        "kind": "derived",
        "bounded": True,
        "dimension_order": list(DIMENSION_ORDER),
        "base_plan": {
            "candidate_id": "base_plan",
            "dimension_results": base_dims,
            "verification_status": "baseline",
        },
        "candidate_results": ranked,
        "selection_note": note,
    }
    if not candidates:
        out["notes"] = ["no_candidates_to_verify"]
    return out
