"""Extract structured records from ``--plan`` reasoning payloads (read-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from axiomurgy.planning import build_plan_summary, resolve_run_target


def _any_improves(
    candidate_rows: Sequence[Mapping[str, Any]],
    dimension: str,
) -> bool:
    for row in candidate_rows:
        for d in row.get("dimension_results") or []:
            if isinstance(d, dict) and d.get("dimension") == dimension and d.get("status") == "improves":
                return True
    return False


def _preferred_from_lullian(cv: Mapping[str, Any]) -> tuple[Optional[str], Optional[str], List[str]]:
    results = [r for r in (cv.get("candidate_results") or []) if isinstance(r, dict)]
    if not results:
        return None, None, []
    by_rank = sorted(results, key=lambda r: int(r.get("rank", 999)))
    top = by_rank[0]
    cid = str(top.get("candidate_id", "")) or None
    ckind = str(top.get("candidate_kind", "")) or None
    statuses = [str(r.get("verification_status", "")) for r in results]
    return cid, ckind, statuses


def capture_plan_reasoning(
    spell_path: Path,
    *,
    artifact_dir: Path,
) -> Dict[str, Any]:
    """
    Build a plan summary (compile + static policy + advisory reasoning) and extract a flat record.

    Does not execute spells, call Vermyth (unless added elsewhere), or write spell files.
    """
    resolved = resolve_run_target(spell_path, None, None, artifact_dir)
    plan = build_plan_summary(resolved)
    return extract_record_from_plan(plan, spell_path=spell_path)


def extract_record_from_plan(
    plan: Mapping[str, Any],
    *,
    spell_path: Path,
    corpus_entry: Optional[Mapping[str, Any]] = None,
    mode: str = "",
) -> Dict[str, Any]:
    """Pure extraction from an already-built plan dict."""
    spell_block = plan.get("spell") or {}
    name = str(spell_block.get("name") or spell_path.stem)
    reasoning = plan.get("reasoning")
    notes: List[str] = []

    reasoning_present = isinstance(reasoning, dict)
    experimental: Optional[Dict[str, Any]] = None
    if reasoning_present:
        experimental = reasoning.get("experimental") if isinstance(reasoning.get("experimental"), dict) else None
    experimental_present = bool(experimental)

    gc = (experimental or {}).get("generation_candidates") or {}
    candidates = [c for c in (gc.get("candidates") or []) if isinstance(c, dict)]
    candidate_count = len(candidates)
    candidate_kinds = [str(c.get("candidate_kind", "")) for c in candidates if c.get("candidate_kind")]

    cv = (experimental or {}).get("candidate_verification") if experimental_present else None
    preferred_id: Optional[str] = None
    preferred_kind: Optional[str] = None
    verification_statuses: List[str] = []
    if isinstance(cv, dict) and cv.get("candidate_results"):
        preferred_id, preferred_kind, verification_statuses = _preferred_from_lullian(cv)
    elif candidates and not isinstance(cv, dict):
        # Generation without Lullian: use first candidate as weak default ordering signal.
        preferred_id = str(candidates[0].get("candidate_id", "")) or None
        preferred_kind = str(candidates[0].get("candidate_kind", "")) or None

    friction_summary: Dict[str, Any] = {}
    telos_summary: Dict[str, Any] = {}
    correspondence_summary: Dict[str, Any] = {}
    wyrd_present = False

    if reasoning_present and isinstance(reasoning, dict):
        telos = reasoning.get("telos") or {}
        dtg = telos.get("distance_to_goal")
        if isinstance(dtg, dict):
            telos_summary = {"distance_to_goal": dtg.get("value"), "unit": dtg.get("unit")}
        else:
            telos_summary = {"distance_to_goal": None}

        if experimental_present and experimental:
            fr = experimental.get("friction") or {}
            of = fr.get("overall_friction") or {}
            friction_summary = {
                "overall_friction_value": of.get("value"),
                "interpretation": of.get("interpretation"),
            }
            corr = experimental.get("correspondence") or {}
            clusters = corr.get("clusters") or []
            correspondence_summary = {
                "cluster_count": len(clusters) if isinstance(clusters, list) else 0,
            }
            wh = experimental.get("wyrd_hints") or {}
            notes_list = list(wh.get("consistency_notes") or [])
            if "wyrd_disabled" in notes_list:
                wyrd_present = False
            else:
                wyrd_present = bool((wh.get("recent_nodes") or []) or (wh.get("related_prior_runs") or []))

    cand_rows: List[Dict[str, Any]] = []
    if isinstance(cv, dict):
        cand_rows = [r for r in (cv.get("candidate_results") or []) if isinstance(r, dict)]

    friction_improvement_signal = _any_improves(cand_rows, "friction_reduction")

    boundary_isolation_improvement_signal = _any_improves(cand_rows, "boundary_isolation")
    approval_positioning_improvement_signal = _any_improves(cand_rows, "approval_positioning")
    objective_alignment_signal = _any_improves(cand_rows, "correspondence_preservation") or _any_improves(
        cand_rows, "telos_coverage"
    )

    output_shape = "full"
    if not reasoning_present:
        output_shape = "no_reasoning"
    elif candidate_count == 0 and experimental_present:
        output_shape = "empty_candidates" if gc.get("generation_enabled") else "minimal"

    family = str((corpus_entry or {}).get("family") or "unspecified")
    expect = (corpus_entry or {}).get("expect") if isinstance((corpus_entry or {}).get("expect"), dict) else {}
    corpus_rel = str((corpus_entry or {}).get("path") or "")

    record: Dict[str, Any] = {
        "spell_name": name,
        "spell_path": str(spell_path.resolve()),
        "corpus_rel_path": corpus_rel,
        "family": family,
        "mode": mode,
        "reasoning_present": reasoning_present,
        "experimental_present": experimental_present,
        "candidate_count": candidate_count,
        "candidate_kinds": candidate_kinds,
        "preferred_candidate_id": preferred_id,
        "preferred_candidate_kind": preferred_kind,
        "verification_statuses": verification_statuses,
        "friction_summary": friction_summary,
        "telos_summary": telos_summary,
        "correspondence_summary": correspondence_summary,
        "wyrd_present": wyrd_present,
        "friction_improvement_signal": bool(friction_improvement_signal),
        "boundary_isolation_improvement_signal": bool(boundary_isolation_improvement_signal),
        "approval_positioning_improvement_signal": bool(approval_positioning_improvement_signal),
        "objective_alignment_signal": bool(objective_alignment_signal),
        "output_shape": output_shape,
        "notes": notes,
    }
    if expect:
        record["expect"] = expect
    return record


def merge_capture_error(
    *,
    mode: str,
    spell_path: Path,
    corpus_entry: Optional[Mapping[str, Any]],
    exc: BaseException,
) -> Dict[str, Any]:
    """Fill a minimal error record when plan build fails."""
    fam = str((corpus_entry or {}).get("family") or "unspecified")
    out: Dict[str, Any] = {
        "spell_name": spell_path.stem,
        "spell_path": str(spell_path.resolve()),
        "corpus_rel_path": str((corpus_entry or {}).get("path") or ""),
        "family": fam,
        "mode": mode,
        "reasoning_present": False,
        "experimental_present": False,
        "candidate_count": 0,
        "candidate_kinds": [],
        "preferred_candidate_id": None,
        "preferred_candidate_kind": None,
        "verification_statuses": [],
        "friction_summary": {},
        "telos_summary": {},
        "correspondence_summary": {},
        "wyrd_present": False,
        "friction_improvement_signal": False,
        "boundary_isolation_improvement_signal": False,
        "approval_positioning_improvement_signal": False,
        "objective_alignment_signal": False,
        "output_shape": "error",
        "notes": [f"capture_error:{type(exc).__name__}:{exc}"],
    }
    if corpus_entry and isinstance(corpus_entry.get("expect"), dict):
        out["expect"] = dict(corpus_entry["expect"])
    return out
