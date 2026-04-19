"""Aggregate bounded metrics over captured records (heuristic, documented)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _rate(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(num / den, 6)


def aggregate_metrics(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Required metrics (bounded / categorical; not fake precision):

    - reasoning_presence_rate
    - experimental_presence_rate
    - candidate_generation_rate (fraction with candidate_count > 0 in modes that enable generation)
    - no_candidate_rate (fraction with candidate_count == 0)
    - candidate_kind_distribution (counts)
    - preferred_candidate_rate (fraction with a preferred id when generation+Lullian expected)
    - preferred_candidate_kind_distribution
    - friction_improvement_signal (count / rate)
    - boundary_isolation_improvement_signal
    - approval_positioning_improvement_signal
    - objective_alignment_signal
    - wyrd_usage_rate (fraction with wyrd_present)
    - error_rate
    """
    n = len(records)
    if n == 0:
        return {
            "n": 0,
            "reasoning_presence_rate": 0.0,
            "experimental_presence_rate": 0.0,
            "candidate_generation_rate": 0.0,
            "no_candidate_rate": 0.0,
            "candidate_kind_distribution": {},
            "preferred_candidate_rate": 0.0,
            "preferred_candidate_kind_distribution": {},
            "friction_improvement_signal_rate": 0.0,
            "boundary_isolation_improvement_signal_rate": 0.0,
            "approval_positioning_improvement_signal_rate": 0.0,
            "objective_alignment_signal_rate": 0.0,
            "wyrd_usage_rate": 0.0,
            "error_rate": 0.0,
            "overgeneration_rate": 0.0,
            "ranking_decisiveness_rate": 0.0,
        }

    reasoning_n = sum(1 for r in records if r.get("reasoning_present"))
    exp_n = sum(1 for r in records if r.get("experimental_present"))
    cand_pos = sum(1 for r in records if int(r.get("candidate_count") or 0) > 0)
    cand_zero = sum(1 for r in records if int(r.get("candidate_count") or 0) == 0)
    err_n = sum(1 for r in records if r.get("output_shape") == "error")

    kinds: Counter[str] = Counter()
    for r in records:
        for k in r.get("candidate_kinds") or []:
            if k:
                kinds[k] += 1

    pref_kinds: Counter[str] = Counter()
    pref_n = 0
    for r in records:
        pk = r.get("preferred_candidate_kind")
        if pk:
            pref_n += 1
            pref_kinds[str(pk)] += 1

    over_n = 0
    for r in records:
        ex = r.get("expect") if isinstance(r.get("expect"), dict) else {}
        if ex.get("no_candidate_expected") and int(r.get("candidate_count") or 0) > 0:
            over_n += 1

    decisive_n = 0
    ranked_n = 0
    for r in records:
        if r.get("mode") not in ("generation_ranked", "generation_ranked_wyrd"):
            continue
        ranked_n += 1
        vs = r.get("verification_statuses") or []
        if vs and str(vs[0]) == "preferred":
            decisive_n += 1

    fi = sum(1 for r in records if r.get("friction_improvement_signal"))
    bi = sum(1 for r in records if r.get("boundary_isolation_improvement_signal"))
    ap = sum(1 for r in records if r.get("approval_positioning_improvement_signal"))
    ob = sum(1 for r in records if r.get("objective_alignment_signal"))
    wy = sum(1 for r in records if r.get("wyrd_present"))

    return {
        "n": n,
        "reasoning_presence_rate": _rate(reasoning_n, n),
        "experimental_presence_rate": _rate(exp_n, n),
        "candidate_generation_rate": _rate(cand_pos, n),
        "no_candidate_rate": _rate(cand_zero, n),
        "candidate_kind_distribution": dict(sorted(kinds.items())),
        "preferred_candidate_rate": _rate(pref_n, n),
        "preferred_candidate_kind_distribution": dict(sorted(pref_kinds.items())),
        "friction_improvement_signal_rate": _rate(fi, n),
        "boundary_isolation_improvement_signal_rate": _rate(bi, n),
        "approval_positioning_improvement_signal_rate": _rate(ap, n),
        "objective_alignment_signal_rate": _rate(ob, n),
        "wyrd_usage_rate": _rate(wy, n),
        "error_rate": _rate(err_n, n),
        "overgeneration_rate": _rate(over_n, n),
        "ranking_decisiveness_rate": _rate(decisive_n, ranked_n) if ranked_n else 0.0,
    }


def compute_cross_mode_metrics(
    by_mode: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """
    Optional cross-mode signals:

    - wyrd_delta_signal: per spell_path, whether preferred kind differs ranked vs ranked_wyrd
    """
    ranked = {r["spell_path"]: r for r in by_mode.get("generation_ranked", []) if "spell_path" in r}
    wyrd = {r["spell_path"]: r for r in by_mode.get("generation_ranked_wyrd", []) if "spell_path" in r}
    delta_paths: List[str] = []
    for path, a in ranked.items():
        b = wyrd.get(path)
        if not b:
            continue
        if (a.get("preferred_candidate_kind") or None) != (b.get("preferred_candidate_kind") or None):
            delta_paths.append(path)
    return {
        "wyrd_preferred_kind_changed_count": len(delta_paths),
        "wyrd_preferred_kind_changed_paths": sorted(delta_paths),
    }


def human_agreement_metrics(
    records: Sequence[Mapping[str, Any]],
    labels_by_path: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """If optional human labels exist, compare to harness ``preferred_candidate_kind``."""
    agree = 0
    total = 0
    for r in records:
        path = str(r.get("spell_path", ""))
        lab = labels_by_path.get(path)
        if not lab:
            continue
        human_kind = lab.get("human_preferred_candidate_kind")
        if human_kind in (None, ""):
            continue
        total += 1
        if str(human_kind) == str(r.get("preferred_candidate_kind") or ""):
            agree += 1
    return {
        "human_labeled_spells": total,
        "human_preferred_kind_agreement_rate": _rate(agree, total) if total else 0.0,
    }
