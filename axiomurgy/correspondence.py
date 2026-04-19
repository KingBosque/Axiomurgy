"""
Correspondence: deterministic mapping from plan clusters to telos objectives (advisory).

Does not reorder compile_plan or mutate spells. Rules are mechanical:
- cluster keys use effect bucket, external boundary (OpenAPI/MCP runes), approval requirement, write surface
- objective links classify each (cluster × objective) as supports / partially_supports / guards
- repeated_patterns only when at least two disjoint read→(transform|verify|simulate|approve)*→write pipelines exist
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Set, Tuple

from .legacy import Spell

# Audit trail: human-readable rule identifiers (no effect on other modules).
DEFAULT_CORRESPONDENCE_RULES: Tuple[str, ...] = (
    "cluster_by_effect_external_approval_write_surface",
    "map_objectives_to_clusters_operational_relationship",
    "emit_repeated_pipeline_motifs_only_with_duplicate_evidence",
)


def _sorted_steps(plan_context: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    steps = list(plan_context.get("steps") or [])
    return sorted(steps, key=lambda r: (int(r.get("index", 0)), str(r.get("step_id", ""))))


def _cluster_key(row: Mapping[str, Any]) -> Tuple[str, bool, bool, bool]:
    rune = str(row.get("rune", ""))
    effect = str(row.get("effect", "transform"))
    pol = row.get("policy") or {}
    ext = rune.startswith("gate.openapi") or rune.startswith("gate.mcp")
    requires_ap = bool(pol.get("requires_approval"))
    eff_bucket = (
        effect
        if effect in ("read", "write", "transform", "verify", "simulate", "approve")
        else "other"
    )
    writes = effect == "write"
    return (eff_bucket, ext, requires_ap, writes)


def _motif(key: Tuple[str, bool, bool, bool]) -> str:
    eff_bucket, ext, _req_ap, writes = key
    if ext:
        return "external_boundary"
    if eff_bucket == "approve":
        return "approval_gate"
    if writes:
        return "write_state"
    if eff_bucket == "read":
        return "local_read"
    if eff_bucket in ("transform", "verify", "simulate"):
        return "local_transform"
    return "other_ops"


def _properties(key: Tuple[str, bool, bool, bool]) -> Dict[str, bool]:
    eff_bucket, ext, requires_ap, writes = key
    return {
        "has_external_boundary": ext,
        "has_approval_gate": requires_ap or eff_bucket == "approve",
        "writes_state": writes,
    }


def _relationship_for_objective(
    objective: Mapping[str, Any],
    motif: str,
    props: Mapping[str, bool],
) -> str:
    """Operational mapping — not metaphysical prose."""
    oid = str(objective.get("id", ""))
    if props["has_external_boundary"] or props["writes_state"]:
        return "guards"
    if props["has_approval_gate"] and motif == "approval_gate":
        return "guards"
    if motif in ("local_read", "local_transform") and not props["has_external_boundary"] and not props["writes_state"]:
        if oid in ("complete_graph", "honor_intent"):
            return "supports"
        return "partially_supports"
    if motif == "other_ops":
        return "partially_supports"
    return "partially_supports"


def _read_middle_write_pipelines(steps: List[Mapping[str, Any]]) -> List[Tuple[int, int]]:
    """Indices into steps (0-based) for disjoint read → middle* → write chains."""
    effects = [str(s.get("effect", "")) for s in steps]
    out: List[Tuple[int, int]] = []
    i = 0
    n = len(effects)
    while i < n:
        if effects[i] != "read":
            i += 1
            continue
        j = i + 1
        while j < n and effects[j] in ("transform", "verify", "simulate", "approve"):
            j += 1
        if j < n and effects[j] == "write":
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def build_correspondence(
    spell: Spell,
    plan_context: Mapping[str, Any],
    telos_view: Mapping[str, Any],
) -> Dict[str, Any]:
    del spell  # reserved for future declared-telos hints; structure uses plan + telos only
    steps = _sorted_steps(plan_context)
    total = max(1, len(steps))

    buckets: Dict[Tuple[str, bool, bool, bool], List[Tuple[int, str]]] = defaultdict(list)
    for row in steps:
        ix = int(row.get("index", 0))
        buckets[_cluster_key(row)].append((ix, str(row["step_id"])))

    key_order = sorted(buckets.keys(), key=lambda k: (min(i for i, _ in buckets[k]), k))

    clusters: List[Dict[str, Any]] = []
    step_to_cluster: Dict[str, str] = {}
    for idx, key in enumerate(key_order):
        cid = f"cl_{idx:04d}"
        pairs = sorted(buckets[key], key=lambda t: (t[0], t[1]))
        sids = [p[1] for p in pairs]
        for sid in sids:
            step_to_cluster[sid] = cid
        m = _motif(key)
        props = _properties(key)
        clusters.append(
            {
                "cluster_id": cid,
                "step_ids": sids,
                "motif": m,
                "properties": props,
                "support_strength": round(len(sids) / total, 4),
            }
        )

    objectives: List[Mapping[str, Any]] = [x for x in (telos_view.get("objectives") or []) if isinstance(x, Mapping)]
    if not objectives:
        objectives = [{"id": "complete_graph", "summary": "", "kind": "derived"}]

    link_acc: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    for cl in clusters:
        cid = str(cl["cluster_id"])
        motif = str(cl["motif"])
        props = cl["properties"]
        if not isinstance(props, dict):
            continue
        for obj in objectives:
            oid = obj.get("id")
            if oid is None:
                continue
            rel = _relationship_for_objective(obj, motif, props)
            link_acc[(cid, rel)].add(str(oid))

    objective_links: List[Dict[str, Any]] = []
    for (cid, rel) in sorted(link_acc.keys()):
        objective_links.append(
            {
                "cluster_id": cid,
                "objective_ids": sorted(link_acc[(cid, rel)]),
                "relationship": rel,
            }
        )

    pipelines = _read_middle_write_pipelines(steps)
    repeated_patterns: List[Dict[str, Any]] = []
    if len(pipelines) >= 2:
        ordered_cids: List[str] = []
        seen_c: Set[str] = set()
        for start_i, end_i in pipelines:
            for pi in range(start_i, end_i + 1):
                sid = str(steps[pi]["step_id"])
                cid = step_to_cluster[sid]
                if cid not in seen_c:
                    seen_c.add(cid)
                    ordered_cids.append(cid)
        repeated_patterns.append(
            {
                "pattern": "read_middle_write_pipeline_duplicated",
                "cluster_ids": ordered_cids,
            }
        )

    return {
        "kind": "derived",
        "clusters": clusters,
        "objective_links": objective_links,
        "repeated_patterns": repeated_patterns,
        "correspondence_rules": list(DEFAULT_CORRESPONDENCE_RULES),
    }
