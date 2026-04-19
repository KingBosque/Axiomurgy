"""
Mechanical mapping from plan-time reasoning JSON into Wyrd v1 nodes and edges.

Mapping rules (append-only audit trail):
- ``telos`` node: ``final_cause``, ``distance_to_goal.value``, objective ``id`` list from ``telos.objectives``.
- ``governor_tradeoff`` nodes: one per entry in ``governor.tradeoffs`` (axis_a, axis_b, resolution).
- ``dialectic_episode`` node: one per ``dialectic.episodes`` (thesis/antithesis/synthesis summaries).
- ``correspondence_cluster`` nodes: one per ``experimental.correspondence.clusters`` (when experimental present).
- ``friction_bottleneck`` nodes: one per ``experimental.friction.bottlenecks``.
- ``review_bundle_ref`` node: when ``plan_out`` contains ``review_bundle_path`` or ``review_bundle_export_path`` (string path).
- ``witness_ref`` node: when spell records witnesses — content holds ``artifact_dir`` relative trace/prov glob for the spell name.
- ``outcome`` node: only when ``plan_out`` exposes an ``execution_outcome`` or ``outcome_summary`` dict (otherwise skipped).

Edges (operational, not metaphor-only):
- ``telos`` --supports--> ``dialectic_episode`` (telos backs the thesis/synthesis framing).
- Each ``governor_tradeoff`` --constrains--> ``dialectic_episode`` (tradeoffs bound the episode).
- ``telos`` --motivates--> each ``governor_tradeoff`` (goal pressure shapes tradeoff surface).
- Each ``correspondence_cluster`` --derives_from--> ``telos`` (clusters align to objectives in telos).
- Each ``friction_bottleneck`` --constrains--> ``telos`` (fragility vs stated goals).
- ``dialectic_episode`` --leads_to--> ``outcome`` when outcome node exists.
- ``witness_ref`` --records--> ``outcome`` when outcome exists; otherwise omitted.

Execution and policy do not read this graph.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from ..legacy import ResolvedRunTarget
from ..util import utc_now
from .store import append_graph_snapshot, stable_edge_id, stable_node_id


def append_reasoning_snapshot(
    resolved: ResolvedRunTarget,
    plan_out: Dict[str, Any],
    reasoning: Dict[str, Any],
) -> Optional[str]:
    """
    Append a compact graph for this plan snapshot. Returns run_id on success.
    Soft-fail: exceptions are caught by the caller; this function should not raise for storage errors
    if the caller wraps — but we use try/except inside and return None on failure.
    """
    try:
        return _append_reasoning_snapshot_impl(resolved, plan_out, reasoning)
    except Exception:
        return None


def _append_reasoning_snapshot_impl(
    resolved: ResolvedRunTarget,
    plan_out: Dict[str, Any],
    reasoning: Dict[str, Any],
) -> str:
    spell = resolved.spell
    spell_name = spell.name
    created_at = utc_now()
    run_id = f"wyrd_{uuid.uuid4().hex}"

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    telos = reasoning.get("telos") or {}
    gov = reasoning.get("governor") or {}
    dia = reasoning.get("dialectic") or {}
    exp = reasoning.get("experimental") or {}

    # --- Telos
    t_key = "telos_root"
    t_content = {
        "final_cause": str(telos.get("final_cause", ""))[:2048],
        "distance_to_goal": (telos.get("distance_to_goal") or {}).get("value"),
        "objective_ids": [str(o.get("id")) for o in (telos.get("objectives") or []) if isinstance(o, dict) and o.get("id")],
    }
    telos_id = stable_node_id(run_id, "telos", t_key, json.dumps(t_content, sort_keys=True))
    nodes.append(
        {
            "node_id": telos_id,
            "kind": "telos",
            "run_id": run_id,
            "spell_name": spell_name,
            "created_at": created_at,
            "content": t_content,
            "source_refs": ["reasoning.telos"],
            "tags": ["plan_snapshot"],
        }
    )

    # --- Governor tradeoffs
    gt_ids: List[str] = []
    for i, tr in enumerate(gov.get("tradeoffs") or []):
        if not isinstance(tr, dict):
            continue
        key = f"gt_{i}"
        c = {
            "axis_a": tr.get("axis_a"),
            "axis_b": tr.get("axis_b"),
            "resolution": tr.get("resolution"),
        }
        nid = stable_node_id(run_id, "governor_tradeoff", key, str(sorted(c.items())))
        gt_ids.append(nid)
        nodes.append(
            {
                "node_id": nid,
                "kind": "governor_tradeoff",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": c,
                "source_refs": [f"reasoning.governor.tradeoffs[{i}]"],
                "tags": ["plan_snapshot"],
            }
        )
        edges.append(
            {
                "edge_id": stable_edge_id(run_id, telos_id, nid, "motivates"),
                "src_node_id": telos_id,
                "dst_node_id": nid,
                "kind": "motivates",
                "run_id": run_id,
                "created_at": created_at,
                "metadata": {"rule": "telos_motivates_tradeoff"},
            }
        )

    # --- Dialectic episode(s)
    dia_ids: List[str] = []
    for i, ep in enumerate(dia.get("episodes") or []):
        if not isinstance(ep, dict):
            continue
        th = (ep.get("thesis") or {}).get("summary", "")
        an = (ep.get("antithesis") or {}).get("summary", "")
        sy = (ep.get("synthesis") or {}).get("summary", "")
        key = f"ep_{i}"
        c = {
            "thesis_summary": str(th)[:512],
            "antithesis_summary": str(an)[:512],
            "synthesis_summary": str(sy)[:512],
        }
        nid = stable_node_id(run_id, "dialectic_episode", key, str(sorted(c.items())))
        dia_ids.append(nid)
        nodes.append(
            {
                "node_id": nid,
                "kind": "dialectic_episode",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": c,
                "source_refs": [f"reasoning.dialectic.episodes[{i}]"],
                "tags": ["plan_snapshot"],
            }
        )
        edges.append(
            {
                "edge_id": stable_edge_id(run_id, telos_id, nid, "supports"),
                "src_node_id": telos_id,
                "dst_node_id": nid,
                "kind": "supports",
                "run_id": run_id,
                "created_at": created_at,
                "metadata": {"rule": "telos_supports_dialectic_episode"},
            }
        )
        for gt in gt_ids:
            edges.append(
                {
                    "edge_id": stable_edge_id(run_id, gt, nid, "constrains"),
                    "src_node_id": gt,
                    "dst_node_id": nid,
                    "kind": "constrains",
                    "run_id": run_id,
                    "created_at": created_at,
                    "metadata": {"rule": "tradeoff_constrains_episode"},
                }
            )

    # --- Correspondence / friction (experimental block)
    co = exp.get("correspondence") or {}
    fr = exp.get("friction") or {}
    cc_ids: List[str] = []
    for i, cl in enumerate(co.get("clusters") or []):
        if not isinstance(cl, dict):
            continue
        key = str(cl.get("cluster_id", f"c{i}"))
        c = {
            "cluster_id": cl.get("cluster_id"),
            "motif": cl.get("motif"),
            "step_ids": cl.get("step_ids"),
            "properties": cl.get("properties"),
        }
        nid = stable_node_id(run_id, "correspondence_cluster", key, str(c))
        cc_ids.append(nid)
        nodes.append(
            {
                "node_id": nid,
                "kind": "correspondence_cluster",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": c,
                "source_refs": [f"reasoning.experimental.correspondence.clusters[{i}]"],
                "tags": ["plan_snapshot"],
            }
        )
        edges.append(
            {
                "edge_id": stable_edge_id(run_id, nid, telos_id, "derives_from"),
                "src_node_id": nid,
                "dst_node_id": telos_id,
                "kind": "derives_from",
                "run_id": run_id,
                "created_at": created_at,
                "metadata": {"rule": "cluster_aligns_objectives_to_telos"},
            }
        )

    for i, bn in enumerate(fr.get("bottlenecks") or []):
        if not isinstance(bn, dict):
            continue
        key = str(bn.get("step_id", f"b{i}"))
        c = {"step_id": bn.get("step_id"), "reason": bn.get("reason")}
        nid = stable_node_id(run_id, "friction_bottleneck", key, str(c))
        nodes.append(
            {
                "node_id": nid,
                "kind": "friction_bottleneck",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": c,
                "source_refs": [f"reasoning.experimental.friction.bottlenecks[{i}]"],
                "tags": ["plan_snapshot"],
            }
        )
        edges.append(
            {
                "edge_id": stable_edge_id(run_id, nid, telos_id, "constrains"),
                "src_node_id": nid,
                "dst_node_id": telos_id,
                "kind": "constrains",
                "run_id": run_id,
                "created_at": created_at,
                "metadata": {"rule": "bottleneck_constrains_telos"},
            }
        )

    # --- Ref / outcome from plan_out (optional)
    outcome_id: Optional[str] = None
    outcome_payload: Optional[Dict[str, Any]] = None
    if isinstance(plan_out.get("execution_outcome"), dict):
        outcome_payload = dict(plan_out["execution_outcome"])
    elif isinstance(plan_out.get("outcome_summary"), dict):
        outcome_payload = dict(plan_out["outcome_summary"])
    if outcome_payload:
        oid = stable_node_id(run_id, "outcome", "outcome_root", str(sorted(outcome_payload.items())))
        outcome_id = oid
        nodes.append(
            {
                "node_id": oid,
                "kind": "outcome",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": outcome_payload,
                "source_refs": ["plan_out.execution_outcome"],
                "tags": ["plan_snapshot"],
            }
        )

    review_path = plan_out.get("review_bundle_path") or plan_out.get("review_bundle_export_path")
    if isinstance(review_path, str) and review_path.strip():
        rid = stable_node_id(run_id, "review_bundle_ref", review_path, "")
        nodes.append(
            {
                "node_id": rid,
                "kind": "review_bundle_ref",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": {"path": review_path},
                "source_refs": ["plan_out.review_bundle_path"],
                "tags": ["plan_snapshot"],
            }
        )

    wcfg = spell.witness or {}
    if isinstance(wcfg, dict) and wcfg.get("record"):
        rel_trace = f"{spell_name}.trace.json"
        rel_prov = f"{spell_name}.prov.json"
        wid = stable_node_id(run_id, "witness_ref", spell_name, rel_trace)
        nodes.append(
            {
                "node_id": wid,
                "kind": "witness_ref",
                "run_id": run_id,
                "spell_name": spell_name,
                "created_at": created_at,
                "content": {
                    "relative_trace": rel_trace,
                    "relative_prov": rel_prov,
                    "artifact_dir": str(resolved.artifact_dir),
                },
                "source_refs": ["spell.witness", "artifact_dir"],
                "tags": ["plan_snapshot"],
            }
        )
        if outcome_id:
            edges.append(
                {
                    "edge_id": stable_edge_id(run_id, wid, outcome_id, "records"),
                    "src_node_id": wid,
                    "dst_node_id": outcome_id,
                    "kind": "records",
                    "run_id": run_id,
                    "created_at": created_at,
                    "metadata": {"rule": "witness_records_outcome"},
                }
            )

    if outcome_id and dia_ids:
        d0 = dia_ids[0]
        edges.append(
            {
                "edge_id": stable_edge_id(run_id, d0, outcome_id, "leads_to"),
                "src_node_id": d0,
                "dst_node_id": outcome_id,
                "kind": "leads_to",
                "run_id": run_id,
                "created_at": created_at,
                "metadata": {"rule": "dialectic_leads_to_outcome"},
            }
        )

    append_graph_snapshot(
        resolved.artifact_dir,
        run_id=run_id,
        spell_name=spell_name,
        nodes=nodes,
        edges=edges,
        created_at=created_at,
    )
    return run_id
