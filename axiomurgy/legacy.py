#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import shutil
import heapq
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import jsonschema
import requests
import yaml

from .fingerprint import (
    classify_input_manifest,
    compute_spell_fingerprints,
    compute_spellbook_fingerprints,
    extract_declared_input_paths,
    extract_output_schema_paths,
)
from .proof import build_proof, build_proof_summary, extract_proofs, normalize_proof
from .runes import MCPClient, REGISTRY, RuneRegistry
from .util import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_POLICY_PATH,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SPELLBOOK_SCHEMA_PATH,
    ROOT,
    _looks_like_path,
    _portable_path_token,
    canonical_json,
    extract_references,
    file_digest_entry,
    json_dumps,
    load_json,
    load_schema,
    load_yaml,
    normalize_paths_for_portability,
    sha256_bytes,
    sha256_file,
    utc_now,
)

VERSION = "2.0.0"
MCP_PROTOCOL_VERSION = "2025-11-25"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
HTTP_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# v0.9 capability-sealed execution: stable, reviewed capability envelope kinds.
CAPABILITY_KINDS = {
    "filesystem.read",
    "filesystem.write",
    "network.http",
    "process.spawn",
    "mcp.resource.read",
    "mcp.tool.call",
    "policy.evaluate",
    "witness.emit",
}


class AxiomurgyError(Exception):
    pass


class SpellValidationError(AxiomurgyError):
    pass


class CapabilityError(AxiomurgyError):
    pass


class PolicyDeniedError(AxiomurgyError):
    pass


class ApprovalRequiredError(AxiomurgyError):
    pass


class StepExecutionError(AxiomurgyError):
    pass


class CapabilityDeniedError(AxiomurgyError):
    def __init__(self, payload: Dict[str, Any]) -> None:
        super().__init__(payload.get("reason") or "Capability denied by vessel.")
        self.payload = payload


class ProofFailure(StepExecutionError):
    def __init__(self, message: str, proof: Dict[str, Any]) -> None:
        super().__init__(message)
        self.proof = proof


@dataclass
class Step:
    step_id: str
    rune: str
    effect: str = "transform"
    args: Dict[str, Any] = field(default_factory=dict)
    requires: List[str] = field(default_factory=list)
    output_schema: Optional[Any] = None
    confidence: Optional[float] = None
    compensates: Optional[str] = None
    description: str = ""


@dataclass
class Spell:
    name: str
    intent: str
    inputs: Dict[str, Any]
    constraints: Dict[str, Any]
    graph: List[Step]
    rollback: List[Step]
    witness: Dict[str, Any]
    source_path: Path


@dataclass
class Spellbook:
    name: str
    version: str
    description: str
    entrypoints: Dict[str, Dict[str, Any]]
    required_capabilities: List[str]
    default_policy: Optional[str]
    validators: List[str]
    artifacts_dir: Optional[str]
    default_entrypoint: Optional[str]
    source_path: Path


@dataclass
class ResolvedRunTarget:
    spell: Spell
    policy_path: Path
    artifact_dir: Path
    spellbook: Optional[Spellbook] = None
    entrypoint: Optional[str] = None


@dataclass
class PolicyDecision:
    allowed: bool = True
    requires_approval: bool = False
    approved: bool = True
    simulated: bool = False
    reasons: List[str] = field(default_factory=list)


@dataclass
class RuneOutcome:
    value: Any
    confidence_factor: float = 1.0
    uncertainty: Optional[str] = None
    side_effect: bool = False


@dataclass
class TraceEvent:
    step_id: str
    rune: str
    effect: str
    status: str
    started_at: str
    ended_at: str
    args: Dict[str, Any]
    output_preview: str
    error: Optional[str]
    policy: Dict[str, Any]
    compensation_for: Optional[str] = None
    compensation_ran: bool = False
    confidence: Optional[float] = None
    entropy: Optional[float] = None
    uncertainty: Optional[str] = None
    proofs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CompensationEvent:
    step_id: str
    rune: str
    status: str
    started_at: str
    ended_at: str
    output_preview: str
    error: Optional[str]


RuneHandler = Callable[["RuneContext", Step, Dict[str, Any]], RuneOutcome]

from .planning import (
    build_approval_manifest,
    build_plan_summary,
    capability_kinds_for_step,
    capability_manifest_for_plan,
    check_spell_capabilities,
    compile_plan,
    evaluate_policy_static,
    external_call_kind,
    load_spell,
    load_spellbook,
    parse_step,
    resolve_run_target,
    resolve_static_reference,
    resolve_static_value,
    rule_matches,
    step_dependencies,
    summarize_write_target,
)
from .describe import (
    build_lint_issue,
    describe_target,
    environment_metadata,
    iter_schema_issues,
    lint_spell_file,
    lint_spellbook,
    lint_target,
)
from .review import (
    build_review_bundle,
    compare_reviewed_bundle,
    compute_attestation,
)
from .execution import (
    RuneContext,
    apply_output_schema,
    build_prov_document,
    build_scxml,
    evaluate_policy,
    execute_spell,
    export_witnesses,
    normalize_proofs_for_diff,
    normalize_prov_for_diff,
    normalize_trace_for_diff,
    run_step,
)


def _parse_run_capsule_config(raw: Any) -> Dict[str, Any]:
    """v1.8 optional run capsule settings (non-breaking defaults). v1.9 adds revolution_retention hook."""
    defaults: Dict[str, Any] = {
        "enabled": True,
        "keep_last_n_runs": None,
        "prune_old_capsules": False,
        "revolution_retention": "preserve_all",
    }
    if raw is None:
        return dict(defaults)
    if not isinstance(raw, dict):
        raise SpellValidationError("run_capsule must be an object when present")
    out = dict(defaults)
    out["enabled"] = bool(raw.get("enabled", True))
    kln = raw.get("keep_last_n_runs")
    if kln is not None:
        out["keep_last_n_runs"] = int(kln)
    out["prune_old_capsules"] = bool(raw.get("prune_old_capsules", False))
    rr = raw.get("revolution_retention")
    if rr is not None:
        out["revolution_retention"] = str(rr)
    return out


def _format_revolution_capsule_id(capsule_index: int) -> str:
    return f"rev_{capsule_index:04d}"


def _next_run_sequence_index(ouroboros_runs_parent: Path) -> int:
    ouroboros_runs_parent.mkdir(parents=True, exist_ok=True)
    best = 0
    for p in ouroboros_runs_parent.iterdir():
        if not p.is_dir():
            continue
        m = re.match(r"^run_(\d{6})$", p.name)
        if m:
            best = max(best, int(m.group(1)))
    return best + 1


def _allocate_ouroboros_run_capsule(base_artifact_dir: Path) -> Tuple[str, Path, int]:
    """Deterministic run_id run_NNNNNN under base_artifact_dir/ouroboros_runs/."""
    parent = (base_artifact_dir / "ouroboros_runs").resolve()
    seq = _next_run_sequence_index(parent)
    run_id = f"run_{seq:06d}"
    capsule = parent / run_id
    capsule.mkdir(parents=True, exist_ok=True)
    return run_id, capsule, seq


def _maybe_prune_old_run_capsules(base_artifact_dir: Path, rc: Dict[str, Any]) -> None:
    if not rc.get("prune_old_capsules"):
        return
    n = rc.get("keep_last_n_runs")
    if not isinstance(n, int) or n <= 0:
        return
    parent = (base_artifact_dir / "ouroboros_runs").resolve()
    if not parent.is_dir():
        return
    dirs = sorted(
        [p for p in parent.iterdir() if p.is_dir() and re.match(r"^run_\d{6}$", p.name)],
        key=lambda p: p.name,
    )
    if len(dirs) <= n:
        return
    for old in dirs[:-n]:
        try:
            shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass


def _paths_relative_to_run_root(run_root: Path, paths: Dict[str, Path]) -> Dict[str, str]:
    rr = run_root.resolve()
    out: Dict[str, str] = {}
    for key, p in paths.items():
        try:
            out[key] = Path(p).resolve().relative_to(rr).as_posix()
        except ValueError:
            out[key] = str(p)
    return out


def _review_bundle_fingerprint(reviewed_bundle: Optional[Dict[str, Any]]) -> Optional[str]:
    if reviewed_bundle is None:
        return None
    return sha256_bytes(canonical_json(reviewed_bundle).encode("utf-8"))


REPLAY_RECORD_SCHEMA_VERSION = "1.0.0"


def _json_safe_replay(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def _snapshot_metric_json_for_replay(art: Path, metric_paths: Set[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for mp in sorted(metric_paths):
        fp = (art / mp) if not Path(mp).is_absolute() else Path(mp)
        try:
            if fp.is_file():
                out[mp] = load_json(fp)
            else:
                out[mp] = {}
        except OSError:
            out[mp] = {}
        except json.JSONDecodeError:
            out[mp] = {}
    return out


def _write_ouroboros_replay_record(
    rev_art: Path,
    *,
    run_id: str,
    revolution_id: str,
    cycle_config_fingerprint: str,
    metric_path: str,
    spell_fingerprints_required: Optional[Dict[str, Any]],
    resolved: ResolvedRunTarget,
    approvals: Set[str],
    simulate: bool,
    enforce_review_bundle: bool,
    reviewed_bundle: Optional[Dict[str, Any]],
    initial_metrics: Dict[str, float],
    metrics_at_best: Dict[str, float],
    metrics_at_last_accept: Dict[str, float],
    rec: Dict[str, Any],
    last_accepted_rec: Optional[Dict[str, Any]],
    best_ordering_index: Optional[int],
    candidate_ordering_index: int,
    revolution: int,
    last_accepted_revolution: Optional[int],
    acc_contract: Dict[str, Any],
    initial_baseline_id: str,
    active_baseline_id: str,
    last_accepted_baseline_id: Optional[str],
    best_primary_at_seal: float,
    metric_files_before_veil: Dict[str, Dict[str, Any]],
    exec_result: Dict[str, Any],
    seal: Dict[str, Any],
    score_after: float,
) -> None:
    recorded_attestation: Optional[Dict[str, Any]] = None
    if reviewed_bundle is not None:
        att = compute_attestation(reviewed_bundle, resolved, approvals=approvals)
        recorded_attestation = {
            "status": att["status"],
            "diff_paths": sorted(
                str(d["path"])
                for d in (att.get("diffs") or [])
                if isinstance(d, dict) and d.get("path") is not None
            ),
        }
    doc: Dict[str, Any] = {
        "schema_version": REPLAY_RECORD_SCHEMA_VERSION,
        "axiomurgy_version": VERSION,
        "run_id": run_id,
        "revolution_id": revolution_id,
        "parent_run_id": run_id,
        "cycle_config_fingerprint": cycle_config_fingerprint,
        "metric_path": metric_path,
        "policy_path": str(resolved.policy_path.resolve()),
        "spell_source_path": str(resolved.spell.source_path.resolve()),
        "review_bundle_fingerprint": _review_bundle_fingerprint(reviewed_bundle),
        "spell_fingerprints_required": _json_safe_replay(spell_fingerprints_required or {}),
        "approvals": sorted(approvals),
        "simulate": simulate,
        "enforce_review_bundle": enforce_review_bundle,
        "seal_inputs": {
            "initial_metrics": {k: float(v) for k, v in sorted(initial_metrics.items())},
            "metrics_at_best": {k: float(v) for k, v in sorted(metrics_at_best.items())},
            "metrics_at_last_accept": {k: float(v) for k, v in sorted(metrics_at_last_accept.items())},
            "rec": _json_safe_replay(rec),
            "last_accepted_rec": _json_safe_replay(last_accepted_rec) if last_accepted_rec is not None else None,
            "best_ordering_index": best_ordering_index,
            "candidate_ordering_index": int(candidate_ordering_index),
            "revolution": revolution,
            "last_accepted_revolution": last_accepted_revolution,
            "acceptance_contract": _json_safe_replay(acc_contract),
            "initial_baseline_id": initial_baseline_id,
            "active_baseline_id": active_baseline_id,
            "last_accepted_baseline_id": last_accepted_baseline_id,
            "best_primary_at_seal": float(best_primary_at_seal),
        },
        "metric_files_before_veil": {k: _json_safe_replay(v) for k, v in sorted(metric_files_before_veil.items())},
        "recorded_score_after": float(score_after),
        "recorded_seal_decision": _json_safe_replay(seal),
        "recorded_execution": _replay_execution_subset_for_compare(exec_result),
        "recorded_attestation": recorded_attestation,
    }
    (rev_art / "replay_record.json").write_text(canonical_json(doc), encoding="utf-8")


def write_ouroboros_run_manifest(
    artifact_dir: Path,
    spell_name: str,
    manifest_doc: Dict[str, Any],
) -> Tuple[Path, Path]:
    raw_path = artifact_dir / f"{spell_name}.run_manifest.raw.json"
    diff_path = artifact_dir / f"{spell_name}.run_manifest.json"
    raw_path.write_text(canonical_json(manifest_doc), encoding="utf-8")
    diff_path.write_text(
        canonical_json(
            normalize_paths_for_portability(json.loads(canonical_json(manifest_doc)), repo_root=ROOT)
        ),
        encoding="utf-8",
    )
    return diff_path, raw_path


ACCEPTANCE_CONTRACT_KEYS = frozenset(
    {"primary_metric", "required_improvement", "guardrails", "tie_breakers", "reject_if"}
)
ACCEPTANCE_TIE_BREAKERS = frozenset(
    {
        "lower_ordering_index",
        "higher_ordering_index",
        "lower_flux_attempts",
        "prefer_admissibility",
    }
)


def _parse_acceptance_contract(
    raw: Any,
    *,
    min_improvement_default: float,
    legacy_tie_break: str,
) -> Dict[str, Any]:
    """Resolved acceptance contract with defaults; backward-compatible when raw is None."""
    legacy_tie_list = (
        ["lower_ordering_index"]
        if legacy_tie_break == "prefer_lower_ordering_index"
        else ["higher_ordering_index"]
    )
    default_reject_if = {
        "score_channel_worsens": False,
        "admissibility_worsens": False,
        "capability_envelope_worsens": False,
    }
    if raw is None:
        return {
            "primary_metric": "maximize",
            "required_improvement": float(min_improvement_default),
            "guardrails": [],
            "tie_breakers": legacy_tie_list,
            "reject_if": dict(default_reject_if),
        }
    if not isinstance(raw, dict):
        raise SpellValidationError("cycle config acceptance_contract must be an object or omitted")
    extra = set(raw.keys()) - ACCEPTANCE_CONTRACT_KEYS
    if extra:
        raise SpellValidationError(f"cycle config acceptance_contract has unknown keys: {sorted(extra)}")
    pm = str(raw.get("primary_metric", "maximize"))
    if pm not in ("maximize", "minimize"):
        raise SpellValidationError("acceptance_contract.primary_metric must be 'maximize' or 'minimize'")
    if "required_improvement" in raw:
        ri = float(raw["required_improvement"])
    else:
        ri = float(min_improvement_default)
    if ri < 0:
        raise SpellValidationError("acceptance_contract.required_improvement must be non-negative")
    grs = raw.get("guardrails", []) or []
    if not isinstance(grs, list):
        raise SpellValidationError("acceptance_contract.guardrails must be a list")
    guardrails: List[Dict[str, Any]] = []
    for i, g in enumerate(grs):
        if not isinstance(g, dict):
            raise SpellValidationError(f"acceptance_contract.guardrails[{i}] must be an object")
        mp = g.get("metric_path")
        if not isinstance(mp, str) or not mp:
            raise SpellValidationError(f"acceptance_contract.guardrails[{i}].metric_path must be a non-empty string")
        comp = str(g.get("comparator", ""))
        if comp not in (">=", ">", "<=", "<", "=="):
            raise SpellValidationError(f"acceptance_contract.guardrails[{i}].comparator must be one of >=, >, <=, <, ==")
        src = str(g.get("baseline_source", ""))
        if src not in ("initial_baseline", "best_so_far", "previous_accepted"):
            raise SpellValidationError(
                f"acceptance_contract.guardrails[{i}].baseline_source must be "
                "initial_baseline, best_so_far, or previous_accepted"
            )
        g_extra = set(g.keys()) - {"metric_path", "comparator", "baseline_source"}
        if g_extra:
            raise SpellValidationError(f"acceptance_contract.guardrails[{i}] unknown keys: {sorted(g_extra)}")
        guardrails.append({"metric_path": mp, "comparator": comp, "baseline_source": src})
    tbr = raw.get("tie_breakers", None)
    if tbr is None:
        tie_breakers = list(legacy_tie_list)
    else:
        if not isinstance(tbr, list) or not tbr:
            raise SpellValidationError("acceptance_contract.tie_breakers must be a non-empty list")
        tie_breakers = []
        for i, tok in enumerate(tbr):
            s = str(tok)
            if s not in ACCEPTANCE_TIE_BREAKERS:
                raise SpellValidationError(
                    f"acceptance_contract.tie_breakers[{i}] must be one of {sorted(ACCEPTANCE_TIE_BREAKERS)}"
                )
            tie_breakers.append(s)
    rj = raw.get("reject_if", {}) or {}
    if not isinstance(rj, dict):
        raise SpellValidationError("acceptance_contract.reject_if must be an object")
    rj_extra = set(rj.keys()) - set(default_reject_if.keys())
    if rj_extra:
        raise SpellValidationError(f"acceptance_contract.reject_if unknown keys: {sorted(rj_extra)}")
    reject_if = {
        "score_channel_worsens": bool(rj.get("score_channel_worsens", False)),
        "admissibility_worsens": bool(rj.get("admissibility_worsens", False)),
        "capability_envelope_worsens": bool(rj.get("capability_envelope_worsens", False)),
    }
    return {
        "primary_metric": pm,
        "required_improvement": ri,
        "guardrails": guardrails,
        "tie_breakers": tie_breakers,
        "reject_if": reject_if,
    }


def load_cycle_config(path: Path) -> Dict[str, Any]:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise SpellValidationError("cycle config must be a JSON object")
    max_rev = int(raw.get("max_revolutions", 1))
    flux_budget = int(raw.get("flux_budget", max_rev))
    plateau_window = int(raw.get("plateau_window", 2))
    stop_conditions = raw.get("stop_conditions", {}) or {}
    if not isinstance(stop_conditions, dict):
        raise SpellValidationError("cycle config stop_conditions must be an object")
    metric = raw.get("target_metric", {}) or {}
    if not isinstance(metric, dict) or metric.get("kind") != "fixture_score":
        raise SpellValidationError("cycle config target_metric.kind must be 'fixture_score'")
    metric_path = metric.get("path")
    if not isinstance(metric_path, str) or not metric_path:
        raise SpellValidationError("cycle config target_metric.path must be a non-empty string")
    allowlist = raw.get("mutation_target_allowlist", []) or []
    if not isinstance(allowlist, list) or not all(isinstance(x, str) for x in allowlist):
        raise SpellValidationError("cycle config mutation_target_allowlist must be a list of strings")

    recall_raw = raw.get("recall", {}) or {}
    if not isinstance(recall_raw, dict):
        raise SpellValidationError("cycle config recall must be an object")
    recent_k_successes = int(recall_raw.get("recent_k_successes", 3))
    recent_k_failures = int(recall_raw.get("recent_k_failures", 3))
    if recent_k_successes < 0 or recent_k_failures < 0:
        raise SpellValidationError("cycle config recall recent_k_* must be non-negative integers")

    tie_break = str(raw.get("tie_break", "prefer_lower_ordering_index"))
    if tie_break not in ("prefer_higher_ordering_index", "prefer_lower_ordering_index"):
        raise SpellValidationError(
            "cycle config tie_break must be 'prefer_higher_ordering_index' or 'prefer_lower_ordering_index'"
        )
    reject_on_noop = bool(raw.get("reject_on_noop", False))

    targets = raw.get("mutation_targets", []) or []
    families = raw.get("mutation_families", []) or []
    if not isinstance(targets, list):
        raise SpellValidationError("cycle config mutation_targets must be a list")
    if not isinstance(families, list):
        raise SpellValidationError("cycle config mutation_families must be a list")
    if families and targets:
        raise SpellValidationError(
            "cycle config must not set both mutation_families and mutation_targets; use one or the other"
        )
    if families:
        for item in families:
            if not isinstance(item, dict):
                raise SpellValidationError("cycle config mutation_families entries must be objects")
            fam = item.get("family")
            if fam not in ("enum", "numeric", "string", "flag", "path_choice"):
                raise SpellValidationError(
                    "cycle config mutation_families[].family must be enum, numeric, string, flag, or path_choice"
                )
            if not isinstance(item.get("path"), str) or not item["path"]:
                raise SpellValidationError("cycle config mutation_families[].path must be a non-empty string")
            cands = item.get("candidates")
            if not isinstance(cands, list) or not cands:
                raise SpellValidationError("cycle config mutation_families[].candidates must be a non-empty list")
            if fam == "numeric":
                for c in cands:
                    if isinstance(c, bool) or not isinstance(c, (int, float)):
                        raise SpellValidationError("cycle config numeric family candidates must be numbers")
            elif fam == "string":
                for c in cands:
                    if not isinstance(c, str):
                        raise SpellValidationError("cycle config string family candidates must be strings")
            elif fam == "flag":
                for c in cands:
                    if not isinstance(c, bool):
                        raise SpellValidationError("cycle config flag family candidates must be booleans")
            elif fam == "path_choice":
                for c in cands:
                    if not isinstance(c, str) or not c:
                        raise SpellValidationError("cycle config path_choice family candidates must be non-empty strings")
    elif targets:
        for item in targets:
            if not isinstance(item, dict):
                raise SpellValidationError("cycle config mutation_targets entries must be objects")
            if not isinstance(item.get("path"), str) or not item["path"]:
                raise SpellValidationError("cycle config mutation_targets[].path must be a non-empty string")
            if not isinstance(item.get("choices"), list) or not item["choices"]:
                raise SpellValidationError("cycle config mutation_targets[].choices must be a non-empty list")
    else:
        raise SpellValidationError("cycle config requires non-empty mutation_families or mutation_targets")

    sens = raw.get("score_channel_sensitive_paths", []) or []
    if not isinstance(sens, list) or not all(isinstance(x, str) for x in sens):
        raise SpellValidationError("cycle config score_channel_sensitive_paths must be a list of strings")
    block_sens = bool(raw.get("block_score_channel_sensitive_mutations", False))

    lineage_pol = raw.get("lineage_policy")
    if lineage_pol is not None and not isinstance(lineage_pol, dict):
        raise SpellValidationError("lineage_policy must be an object when present")

    run_capsule_cfg = _parse_run_capsule_config(raw.get("run_capsule"))

    min_imp = float(stop_conditions.get("min_improvement", 0.0))
    acceptance_contract = _parse_acceptance_contract(
        raw.get("acceptance_contract"),
        min_improvement_default=min_imp,
        legacy_tie_break=tie_break,
    )

    return {
        "max_revolutions": max_rev,
        "flux_budget": flux_budget,
        "plateau_window": plateau_window,
        "target_metric": {"kind": "fixture_score", "path": metric_path},
        "rollback_mode": str(raw.get("rollback_mode", "shadow_copy")),
        "require_approval_for_accept": bool(raw.get("require_approval_for_accept", False)),
        "mutation_target_allowlist": list(allowlist),
        "mutation_targets": targets,
        "mutation_families": families,
        "recall": {
            "recent_k_successes": recent_k_successes,
            "recent_k_failures": recent_k_failures,
        },
        "tie_break": tie_break,
        "reject_on_noop": reject_on_noop,
        "stop_conditions": {
            "max_failures": int(stop_conditions.get("max_failures", max_rev)),
            "min_improvement": min_imp,
            "no_improve_for": int(stop_conditions.get("no_improve_for", plateau_window)),
        },
        "score_channel_sensitive_paths": list(sens),
        "block_score_channel_sensitive_mutations": block_sens,
        "acceptance_contract": acceptance_contract,
        "lineage_policy": dict(lineage_pol or {}),
        "run_capsule": run_capsule_cfg,
    }


def expand_cycle_proposals(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic ordered list of mutation proposals (family, path, candidate)."""
    out: List[Dict[str, Any]] = []
    ordering_index = 0
    if cfg.get("mutation_families"):
        for item in cfg["mutation_families"]:
            fam = str(item["family"])
            path = str(item["path"])
            for candidate in item["candidates"]:
                pid = proposal_id(fam, path, candidate)
                out.append(
                    {
                        "ordering_index": ordering_index,
                        "family": fam,
                        "path": path,
                        "candidate": candidate,
                        "proposal_id": pid,
                    }
                )
                ordering_index += 1
    else:
        for item in cfg["mutation_targets"]:
            path = str(item["path"])
            for candidate in item["choices"]:
                pid = proposal_id("enum", path, candidate)
                out.append(
                    {
                        "ordering_index": ordering_index,
                        "family": "enum",
                        "path": path,
                        "candidate": candidate,
                        "proposal_id": pid,
                    }
                )
                ordering_index += 1
    out.sort(key=lambda p: p["ordering_index"])
    # Suppress duplicate proposal_id (same logical mutation); keep earliest ordering_index.
    seen_ids: Set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for p in out:
        pid = p["proposal_id"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        deduped.append(p)
    return deduped


def _canonical_candidate_for_proposal_id(candidate: Any) -> Any:
    """Normalize JSON number shapes so 1 and 1.0 yield the same proposal_id."""
    if isinstance(candidate, bool):
        return candidate
    if isinstance(candidate, int):
        return float(candidate)
    if isinstance(candidate, float):
        return float(candidate)
    return candidate


def proposal_id(family: str, path: str, candidate: Any) -> str:
    c = _canonical_candidate_for_proposal_id(candidate)
    payload = canonical_json({"candidate": c, "family": family, "path": path})
    return sha256_bytes(payload.encode("utf-8"))


def _cycle_allowlisted(path: str, allowlist: List[str]) -> bool:
    if not allowlist:
        return False
    return any(fnmatch.fnmatch(path, pat) for pat in allowlist)


def _set_mutation(spell: Spell, mutation_path: str, value: Any, allowlist: List[str]) -> None:
    if not _cycle_allowlisted(mutation_path, allowlist):
        raise SpellValidationError(f"Mutation target not allowlisted: {mutation_path}")
    parts = mutation_path.split(".")
    if parts[:2] == ["spell", "inputs"] and len(parts) >= 3:
        key = ".".join(parts[2:])
        spell.inputs[key] = value
        return
    if parts[:2] == ["spell", "graph"] and len(parts) >= 5 and parts[3] == "args":
        step_id = parts[2]
        arg_key = ".".join(parts[4:])
        for step in spell.graph:
            if step.step_id == step_id:
                step.args[arg_key] = value
                return
        raise SpellValidationError(f"Mutation step not found: {step_id}")
    raise SpellValidationError(f"Unsupported mutation path: {mutation_path}")


def _get_mutation_value(spell: Spell, mutation_path: str) -> Any:
    parts = mutation_path.split(".")
    if parts[:2] == ["spell", "inputs"] and len(parts) >= 3:
        key = ".".join(parts[2:])
        return spell.inputs.get(key)
    if parts[:2] == ["spell", "graph"] and len(parts) >= 5 and parts[3] == "args":
        step_id = parts[2]
        arg_key = ".".join(parts[4:])
        for step in spell.graph:
            if step.step_id == step_id:
                return step.args.get(arg_key)
        return None
    raise SpellValidationError(f"Unsupported mutation path: {mutation_path}")


def _fixture_score(artifact_dir: Path, metric_path: str) -> float:
    p = Path(metric_path)
    p = p if p.is_absolute() else (artifact_dir / p)
    doc = load_json(p)
    if not isinstance(doc, dict) or "score" not in doc:
        raise StepExecutionError(f"fixture_score missing numeric 'score' in {p}")
    return float(doc["score"])


def _ouroboros_recall_snapshot(
    *,
    recent_successes: deque,
    recent_failures: deque,
    best_score_so_far: float,
    current_plateau_length: int,
    accepted_mutation_count: int,
    rejected_mutation_count: int,
) -> Dict[str, Any]:
    return {
        "recent_k_successes": list(recent_successes),
        "recent_k_failures": list(recent_failures),
        "best_score_so_far": best_score_so_far,
        "current_plateau_length": current_plateau_length,
        "accepted_mutation_count": accepted_mutation_count,
        "rejected_mutation_count": rejected_mutation_count,
    }


def _scores_equal(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-12


def _fixture_score_safe(artifact_dir: Path, metric_path: str) -> float:
    try:
        return _fixture_score(artifact_dir, metric_path)
    except Exception:
        return float("-inf")


def _admissibility_rank(status: str) -> int:
    return {"admissible": 0, "uncertain": 1, "inadmissible": 2}.get(status, 1)


def _score_channel_rank(status: str) -> int:
    return {"aligned": 0, "not_aligned": 1, "uncertain": 2}.get(status, 2)


def _capability_envelope_rank(status: str) -> int:
    return {"compatible": 0, "not_applicable": 1, "unknown": 2, "incompatible": 3}.get(status, 2)


def _compare_floats(a: float, b: float, op: str) -> bool:
    if op == ">=":
        return a >= b
    if op == ">":
        return a > b
    if op == "<=":
        return a <= b
    if op == "<":
        return a < b
    if op == "==":
        return _scores_equal(a, b)
    return False


def _evaluate_guardrails(
    artifact_dir: Path,
    guardrails: List[Dict[str, Any]],
    initial_metrics: Dict[str, float],
    metrics_at_best: Dict[str, float],
    metrics_at_last_accept: Dict[str, float],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    out: List[Dict[str, Any]] = []
    ref_map: Dict[str, str] = {}
    any_fail = False
    baseline_used = {"primary": "best_so_far", "guardrails": ref_map}
    for g in guardrails:
        mp = str(g["metric_path"])
        comp = str(g["comparator"])
        src = str(g["baseline_source"])
        cand = _fixture_score_safe(artifact_dir, mp)
        if src == "initial_baseline":
            base = initial_metrics.get(mp, float("-inf"))
            ref_map[mp] = "initial_baseline"
        elif src == "best_so_far":
            base = metrics_at_best.get(mp, float("-inf"))
            ref_map[mp] = "best_so_far"
        else:
            base = metrics_at_last_accept.get(mp, float("-inf"))
            ref_map[mp] = "previous_accepted"
        ok = _compare_floats(cand, base, comp)
        if not ok:
            any_fail = True
        out.append(
            {
                "metric_path": mp,
                "comparator": comp,
                "baseline_source": src,
                "candidate_value": cand,
                "baseline_value": base,
                "pass": ok,
            }
        )
    return out, baseline_used, any_fail


def evaluate_acceptance_contract(
    *,
    artifact_dir: Path,
    contract: Dict[str, Any],
    execution_succeeded: bool,
    candidate_primary: float,
    best_primary: float,
    initial_metrics: Dict[str, float],
    metrics_at_best: Dict[str, float],
    metrics_at_last_accept: Dict[str, float],
    rec: Dict[str, Any],
    last_accepted_rec: Optional[Dict[str, Any]],
    best_ordering_index: Optional[int],
    candidate_ordering_index: int,
    revolution: int,
    last_accepted_revolution: Optional[int],
) -> Dict[str, Any]:
    """
    Deterministic seal-stage acceptance (v1.6+).
    Order: execution failure → reject_if (vs last accepted) → primary strict improvement
    (with guardrails) → equal-primary path with guardrails then tie_breakers.
    v1.7: the chamber enriches each seal with baseline_reference_used_id (concrete baseline_ids).
    """
    pm_mode = str(contract.get("primary_metric", "maximize"))
    req_imp = float(contract.get("required_improvement", 0.0))
    guardrails = list(contract.get("guardrails") or [])
    tie_breakers = list(contract.get("tie_breakers") or ["lower_ordering_index"])
    reject_if = contract.get("reject_if") or {}

    if not execution_succeeded:
        return {
            "decision": "reject",
            "primary_metric_result": {
                "mode": pm_mode,
                "candidate": candidate_primary,
                "reference": best_primary,
                "reference_label": "best_so_far",
                "passes_required_improvement": False,
            },
            "guardrail_results": [],
            "reject_if_results": {},
            "tie_break_results": [],
            "baseline_reference_used": {"primary": "best_so_far", "guardrails": {}},
            "reasons": ["execution_failed"],
        }

    reasons: List[str] = []
    reject_if_results: Dict[str, Any] = {}
    if last_accepted_rec is not None:
        if reject_if.get("score_channel_worsens"):
            prev = _score_channel_rank(str(last_accepted_rec.get("score_channel_status")))
            cur = _score_channel_rank(str(rec.get("score_channel_status")))
            worsens = cur > prev
            reject_if_results["score_channel_worsens"] = {"triggered": worsens, "previous_rank": prev, "current_rank": cur}
            if worsens:
                reasons.append("reject_if:score_channel_worsens")
        else:
            reject_if_results["score_channel_worsens"] = {"triggered": False}
        if reject_if.get("admissibility_worsens"):
            prev = _admissibility_rank(str(last_accepted_rec.get("admissibility_status")))
            cur = _admissibility_rank(str(rec.get("admissibility_status")))
            worsens = cur > prev
            reject_if_results["admissibility_worsens"] = {"triggered": worsens, "previous_rank": prev, "current_rank": cur}
            if worsens:
                reasons.append("reject_if:admissibility_worsens")
        else:
            reject_if_results["admissibility_worsens"] = {"triggered": False}
        if reject_if.get("capability_envelope_worsens"):
            prev = _capability_envelope_rank(str(last_accepted_rec.get("capability_envelope_compatibility")))
            cur = _capability_envelope_rank(str(rec.get("capability_envelope_compatibility")))
            worsens = cur > prev
            reject_if_results["capability_envelope_worsens"] = {
                "triggered": worsens,
                "previous_rank": prev,
                "current_rank": cur,
            }
            if worsens:
                reasons.append("reject_if:capability_envelope_worsens")
        else:
            reject_if_results["capability_envelope_worsens"] = {"triggered": False}
    else:
        reject_if_results = {
            "score_channel_worsens": {"triggered": False, "note": "no_previous_accept"},
            "admissibility_worsens": {"triggered": False, "note": "no_previous_accept"},
            "capability_envelope_worsens": {"triggered": False, "note": "no_previous_accept"},
        }

    if any(r.startswith("reject_if:") for r in reasons):
        return {
            "decision": "reject",
            "primary_metric_result": {
                "mode": pm_mode,
                "candidate": candidate_primary,
                "reference": best_primary,
                "reference_label": "best_so_far",
                "passes_required_improvement": False,
            },
            "guardrail_results": [],
            "reject_if_results": reject_if_results,
            "tie_break_results": [],
            "baseline_reference_used": {"primary": "best_so_far", "guardrails": {}},
            "reasons": reasons,
        }

    ref_primary = best_primary
    passes_strict = False
    if pm_mode == "maximize":
        passes_strict = candidate_primary > ref_primary + req_imp
    else:
        if ref_primary == float("-inf"):
            passes_strict = candidate_primary != float("-inf")
        else:
            passes_strict = candidate_primary < ref_primary - req_imp

    primary_metric_result = {
        "mode": pm_mode,
        "candidate": candidate_primary,
        "reference": ref_primary,
        "reference_label": "best_so_far",
        "passes_required_improvement": passes_strict,
    }

    equal_primary = _scores_equal(candidate_primary, ref_primary) or (
        candidate_primary == float("-inf") and ref_primary == float("-inf")
    )

    if passes_strict:
        gr_out, gr_baseline, gr_fail = _evaluate_guardrails(
            artifact_dir, guardrails, initial_metrics, metrics_at_best, metrics_at_last_accept
        )
        if gr_fail:
            return {
                "decision": "reject",
                "primary_metric_result": primary_metric_result,
                "guardrail_results": gr_out,
                "reject_if_results": reject_if_results,
                "tie_break_results": [],
                "baseline_reference_used": gr_baseline,
                "reasons": ["contract_reject:guardrail"],
            }
        return {
            "decision": "accept",
            "primary_metric_result": primary_metric_result,
            "guardrail_results": gr_out,
            "reject_if_results": reject_if_results,
            "tie_break_results": [],
            "baseline_reference_used": gr_baseline,
            "reasons": ["contract_accept:primary_strict"],
        }

    gr_out, gr_baseline, gr_fail = _evaluate_guardrails(
        artifact_dir, guardrails, initial_metrics, metrics_at_best, metrics_at_last_accept
    )
    if gr_fail:
        return {
            "decision": "reject",
            "primary_metric_result": primary_metric_result,
            "guardrail_results": gr_out,
            "reject_if_results": reject_if_results,
            "tie_break_results": [],
            "baseline_reference_used": gr_baseline,
            "reasons": ["contract_reject:guardrail"],
        }

    if not equal_primary:
        return {
            "decision": "reject",
            "primary_metric_result": primary_metric_result,
            "guardrail_results": gr_out,
            "reject_if_results": reject_if_results,
            "tie_break_results": [],
            "baseline_reference_used": gr_baseline,
            "reasons": ["contract_reject:primary_not_improved"],
        }

    tie_break_results: List[Dict[str, Any]] = []
    accept_tie = False
    if best_ordering_index is None:
        accept_tie = True
        tie_break_results.append({"rule": "no_prior_best_ordering", "accept": True})
    else:
        for rule in tie_breakers:
            tr: Dict[str, Any] = {"rule": rule, "accept": False}
            if rule == "lower_ordering_index":
                tr["accept"] = candidate_ordering_index < best_ordering_index
            elif rule == "higher_ordering_index":
                tr["accept"] = candidate_ordering_index > best_ordering_index
            elif rule == "lower_flux_attempts":
                if last_accepted_revolution is None:
                    tr["accept"] = True
                else:
                    tr["accept"] = revolution < last_accepted_revolution
            elif rule == "prefer_admissibility":
                if last_accepted_rec is None:
                    tr["accept"] = True
                else:
                    tr["accept"] = _admissibility_rank(str(rec.get("admissibility_status"))) < _admissibility_rank(
                        str(last_accepted_rec.get("admissibility_status"))
                    )
            tie_break_results.append(tr)
            if tr["accept"]:
                accept_tie = True
                break

    if accept_tie:
        return {
            "decision": "accept",
            "primary_metric_result": primary_metric_result,
            "guardrail_results": gr_out,
            "reject_if_results": reject_if_results,
            "tie_break_results": tie_break_results,
            "baseline_reference_used": gr_baseline,
            "reasons": ["contract_accept:tie_break"],
        }

    return {
        "decision": "reject",
        "primary_metric_result": primary_metric_result,
        "guardrail_results": gr_out,
        "reject_if_results": reject_if_results,
        "tie_break_results": tie_break_results,
        "baseline_reference_used": gr_baseline,
        "reasons": ["contract_reject:tie_break"],
    }


def _record_seal_acceptance_summary(summary: Dict[str, int], seal: Dict[str, Any]) -> None:
    if seal.get("decision") == "accept":
        summary["accepted_by_contract"] += 1
        return
    reasons = seal.get("reasons") or []
    r0 = reasons[0] if reasons else ""
    if r0 == "contract_reject:guardrail":
        summary["rejected_by_guardrail"] += 1
    elif r0 == "contract_reject:tie_break":
        summary["rejected_by_tiebreak"] += 1
    elif r0 == "reject_if:score_channel_worsens":
        summary["rejected_by_score_channel"] += 1
    elif r0.startswith("reject_if:"):
        summary["rejected_by_reject_if_non_score"] += 1
    else:
        summary["rejected_by_contract"] += 1


def _format_baseline_id(logical_index: int) -> str:
    """Deterministic baseline id for Ouroboros lineage (v1.7)."""
    return f"bl_{logical_index:04d}"


def _format_promotion_id(seq: int) -> str:
    """Deterministic promotion record id (v1.7)."""
    return f"pr_{seq:04d}"


def _guardrail_metrics_snapshot(metrics: Dict[str, float], metric_paths_set: Set[str]) -> Dict[str, float]:
    return {p: float(metrics.get(p, float("-inf"))) for p in sorted(metric_paths_set)}


def _enrich_seal_baseline_reference_ids(
    seal: Dict[str, Any],
    *,
    initial_baseline_id: str,
    active_baseline_id: str,
    last_accepted_baseline_id: Optional[str],
) -> None:
    """
    Resolve acceptance-contract baseline labels to concrete baseline_ids (v1.7).
    Mutates seal in place: adds baseline_reference_used_id alongside baseline_reference_used.
    """
    prev_accept_target = last_accepted_baseline_id if last_accepted_baseline_id is not None else initial_baseline_id
    primary_id = active_baseline_id
    gr_ids: Dict[str, str] = {}
    for g in seal.get("guardrail_results") or []:
        if not isinstance(g, dict):
            continue
        mp = str(g.get("metric_path", ""))
        if not mp:
            continue
        src = str(g.get("baseline_source", ""))
        if src == "initial_baseline":
            gr_ids[mp] = initial_baseline_id
        elif src == "best_so_far":
            gr_ids[mp] = active_baseline_id
        elif src == "previous_accepted":
            gr_ids[mp] = prev_accept_target
        else:
            gr_ids[mp] = active_baseline_id
    seal["baseline_reference_used_id"] = {
        "primary": primary_id,
        "guardrails": gr_ids,
    }


def _slim_seal_for_promotion(seal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "decision": seal.get("decision"),
        "reasons": list(seal.get("reasons") or []),
        "primary_metric_result": seal.get("primary_metric_result"),
        "tie_break_results": seal.get("tie_break_results"),
        "reject_if_results": seal.get("reject_if_results"),
        "baseline_reference_used": seal.get("baseline_reference_used"),
        "baseline_reference_used_id": seal.get("baseline_reference_used_id"),
    }


def _lineage_summary_top(
    baseline_registry: List[Dict[str, Any]],
    promotion_records: List[Dict[str, Any]],
    final_active_baseline_id: str,
) -> Dict[str, Any]:
    return {
        "total_baselines_created": len(baseline_registry),
        "total_promotions": len(promotion_records),
        "final_active_baseline_id": final_active_baseline_id,
        "superseded_baseline_count": sum(1 for r in baseline_registry if r.get("status") == "superseded"),
    }


def _predicted_capability_kinds_for_spell(spell: Spell) -> List[str]:
    plan = compile_plan(spell)
    kinds: Set[str] = set()
    for step in plan:
        kinds |= capability_kinds_for_step(step)
    return sorted(kinds)


def _mutation_target_class(mutation_path: str) -> str:
    if mutation_path.startswith("spell.inputs."):
        return "inputs"
    parts = mutation_path.split(".")
    if len(parts) >= 5 and parts[0] == "spell" and parts[1] == "graph" and parts[3] == "args":
        return "graph_args"
    return "other"


def _effect_signature_canonical(spell: Spell, mutation_path: str) -> Dict[str, Any]:
    """Mechanical effect-signature: capabilities, plan shape, and mutation locus (not candidate values)."""
    plan = compile_plan(spell)
    predicted = _predicted_capability_kinds_for_spell(spell)
    step_effects = sorted([[s.step_id, str(s.effect)] for s in plan])
    step_runes = sorted([[s.step_id, str(s.rune)] for s in plan])
    write_effect_step_count = sum(1 for s in plan if str(s.effect) == "write")
    mt = _mutation_target_class(mutation_path)
    parts = mutation_path.split(".")
    mutation_input_key: Optional[str] = None
    mutation_graph_step_id: Optional[str] = None
    mutation_graph_arg_key: Optional[str] = None
    if mt == "inputs" and len(parts) >= 3:
        mutation_input_key = ".".join(parts[2:])
    elif mt == "graph_args" and len(parts) >= 5:
        mutation_graph_step_id = parts[2]
        mutation_graph_arg_key = ".".join(parts[4:])
    return {
        "predicted_capabilities": predicted,
        "step_effects": step_effects,
        "step_runes": step_runes,
        "write_effect_step_count": write_effect_step_count,
        "mutation_target_class": mt,
        "mutation_input_key": mutation_input_key,
        "mutation_graph_step_id": mutation_graph_step_id,
        "mutation_graph_arg_key": mutation_graph_arg_key,
    }


def _effect_signature_id(canonical: Dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(canonical).encode("utf-8"))


def _round_robin_by_effect_signature(tier_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Within a tier, interleave proposals so distinct effect signatures alternate before repeats (deterministic)."""
    by_sig: Dict[str, List[Dict[str, Any]]] = {}
    for r in sorted(tier_records, key=lambda x: int(x["ordering_index"])):
        sid = str(r["effect_signature_id"])
        by_sig.setdefault(sid, []).append(r)
    sig_ids = sorted(
        by_sig.keys(),
        key=lambda s: min(int(x["ordering_index"]) for x in by_sig[s]),
    )
    out: List[Dict[str, Any]] = []
    while any(by_sig[s] for s in sig_ids):
        for s in sig_ids:
            if by_sig[s]:
                out.append(by_sig[s].pop(0))
    return out


def _finalize_diversified_ranking(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tiers = ["admissible", "uncertain", "inadmissible"]
    ranked: List[Dict[str, Any]] = []
    per_tier: Dict[str, Any] = {}
    for status in tiers:
        tier = [r for r in records if r["admissibility_status"] == status]
        if not tier:
            continue
        rr = _round_robin_by_effect_signature(tier)
        first_pid: Dict[str, str] = {}
        sig_rank: Dict[str, int] = {}
        next_rk = 0
        for r in rr:
            sid = str(r["effect_signature_id"])
            if sid not in first_pid:
                first_pid[sid] = str(r["proposal_id"])
                sig_rank[sid] = next_rk
                next_rk += 1
                r["duplicate_of_signature"] = None
            else:
                r["duplicate_of_signature"] = first_pid[sid]
            r["signature_rank"] = int(sig_rank[sid])
        distinct = len({str(r["effect_signature_id"]) for r in tier})
        per_tier[status] = {"proposals": len(tier), "distinct_effect_signatures": distinct}
        ranked.extend(rr)
    summary = {
        "per_tier": per_tier,
        "diversification_mode": "round_robin_by_effect_signature_within_admissibility_tier",
    }
    return ranked, summary


def _maybe_path_for_spell(spell: Spell, raw: str) -> Path:
    """Match RuneContext.maybe_path: absolute paths stay; relative resolve against spell source parent."""
    path = Path(raw)
    if path.is_absolute():
        return path
    return (spell.source_path.parent / path).resolve()


def _resolve_gate_file_write_records(spell: Spell) -> List[Dict[str, Any]]:
    """Static resolution of gate.file_write path args; compares to metric_abs via normalized strings."""
    out: List[Dict[str, Any]] = []
    for step in compile_plan(spell):
        if step.rune != "gate.file_write":
            continue
        static_args = resolve_static_value(step.args, {"inputs": spell.inputs})
        raw_path = static_args.get("path") if isinstance(static_args, dict) else None
        rec: Dict[str, Any] = {
            "step_id": step.step_id,
            "effect": str(step.effect),
            "rune": step.rune,
            "resolved": False,
            "resolved_path": None,
            "path_resolution_reason": None,
        }
        if not isinstance(raw_path, str):
            rec["path_resolution_reason"] = "path_not_a_string"
            out.append(rec)
            continue
        if raw_path.startswith("$"):
            rec["path_resolution_reason"] = "unresolved_path_reference"
            out.append(rec)
            continue
        try:
            abs_p = _maybe_path_for_spell(spell, raw_path).resolve()
        except OSError:
            rec["path_resolution_reason"] = "path_resolve_error"
            out.append(rec)
            continue
        rec["resolved"] = True
        rec["resolved_path"] = str(abs_p)
        out.append(rec)
    return out


def _analyze_score_channel_for_shadow(
    spell: Spell,
    *,
    metric_kind: str,
    metric_rel: str,
    metric_abs: Path,
) -> Dict[str, Any]:
    """
    Mechanical score channel vs fixture_score metric file (metric_abs).
    score_channel_status: aligned | not_aligned | uncertain
    """
    metric_abs_s = str(metric_abs.resolve())
    records = _resolve_gate_file_write_records(spell)
    writers: List[str] = []
    unresolved_any = any(
        not r["resolved"] for r in records
    )
    for r in records:
        if r["resolved"] and r.get("resolved_path") == metric_abs_s:
            writers.append(str(r["step_id"]))
    writers = sorted(set(writers))

    metric_source_step_id: Optional[str] = None
    metric_source_effect: Optional[str] = None
    if len(writers) == 1:
        metric_source_step_id = writers[0]
        for step in compile_plan(spell):
            if step.step_id == metric_source_step_id:
                metric_source_effect = str(step.effect)
                break

    reasons: List[str] = []
    status: str
    if not records:
        status = "uncertain"
        reasons.append("no_gate_file_write_steps")
    elif unresolved_any:
        status = "uncertain"
        reasons.append("unresolved_write_path")
    elif len(writers) == 1:
        status = "aligned"
        reasons.append("single_writer_to_metric_file")
    elif len(writers) == 0:
        status = "not_aligned"
        reasons.append("no_writer_to_metric_file")
    else:
        status = "uncertain"
        reasons.append("multiple_writers_to_metric_file")

    return {
        "metric_kind": metric_kind,
        "metric_path": metric_rel,
        "metric_abs": metric_abs_s,
        "write_path_records": records,
        "writers_to_metric": writers,
        "metric_source_step_id": metric_source_step_id,
        "metric_source_effect": metric_source_effect,
        "score_channel_status": status,
        "score_channel_reasons": reasons,
    }


def _baseline_shadow_for_ouroboros(base: Spell, metric_abs: str) -> Spell:
    shadow = load_spell(base.source_path) if base.source_path.exists() else base
    shadow.inputs = dict(base.inputs)
    shadow.inputs["score_path"] = metric_abs
    shadow.graph = [Step(**step.__dict__) for step in base.graph]
    return shadow


def _score_channel_clear_break(
    baseline: Dict[str, Any],
    proposal: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """
    Inadmissible only when: baseline aligned, proposal has zero writers to metric,
    and proposal has no unresolved gate.file_write path refs.
    """
    if baseline.get("score_channel_status") != "aligned":
        return False, []
    prop_records = proposal.get("write_path_records") or []
    if any(not r.get("resolved") for r in prop_records):
        return False, []
    writers = proposal.get("writers_to_metric") or []
    if len(writers) != 0:
        return False, []
    return True, ["score_channel_clear_break"]


def _score_channel_diff_payload(
    baseline: Dict[str, Any],
    proposal: Dict[str, Any],
    mutation_target: str,
) -> Dict[str, Any]:
    return {
        "mutation_target": mutation_target,
        "baseline_writers_to_metric": list(baseline.get("writers_to_metric") or []),
        "proposal_writers_to_metric": list(proposal.get("writers_to_metric") or []),
    }


def _mutation_matches_score_channel_sensitive(mutation_path: str, patterns: List[str]) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatch(mutation_path, pat) for pat in patterns)


def _build_shadow_spell_for_ouroboros_proposal(
    base: Spell,
    *,
    metric_abs: str,
    mutation_path: str,
    candidate: Any,
    allowlist: List[str],
) -> Spell:
    shadow_spell = load_spell(base.source_path) if base.source_path.exists() else base
    shadow_spell.inputs = dict(base.inputs)
    shadow_spell.inputs["score_path"] = metric_abs
    shadow_spell.graph = [Step(**step.__dict__) for step in base.graph]
    _set_mutation(shadow_spell, mutation_path, candidate, allowlist)
    return shadow_spell


def _admissibility_status_rank(status: str) -> int:
    return {"admissible": 0, "uncertain": 1, "inadmissible": 2}.get(status, 1)


def plan_ouroboros_proposals(
    resolved: ResolvedRunTarget,
    *,
    proposals: List[Dict[str, Any]],
    allowlist: List[str],
    metric_abs: str,
    metric_rel: str,
    reviewed_bundle: Optional[Dict[str, Any]],
    enforce_review_bundle: bool,
    score_channel_sensitive_paths: Optional[List[str]] = None,
    block_score_channel_sensitive_mutations: bool = False,
) -> Dict[str, Any]:
    """
    Deterministic pre-revolution admissibility planning (v1.5).
    Does not execute steps; mirrors vessel overreach check at plan level.
    Score-channel analysis ties fixture_score reads to gate.file_write targets at metric_abs.
    Ranks proposals by admissibility tier, then diversifies by effect signature (round-robin).
    """
    base = resolved.spell
    sens_paths = list(score_channel_sensitive_paths or [])
    reviewed_kinds: Optional[Set[str]] = None
    envelope_present = False
    if reviewed_bundle is not None:
        env = (reviewed_bundle.get("capabilities") or {}).get("envelope") or {}
        kinds = env.get("kinds")
        if isinstance(kinds, list):
            envelope_present = True
            reviewed_kinds = {str(k) for k in kinds}

    fp_match = True
    if reviewed_bundle is not None:
        rf = (reviewed_bundle.get("fingerprints") or {}).get("required") or {}
        cf = compute_spell_fingerprints(base, resolved.policy_path, repo_root=ROOT).get("required") or {}
        rs = rf.get("spell_sha256")
        cs = cf.get("spell_sha256")
        if isinstance(rs, str) and isinstance(cs, str) and rs != cs:
            fp_match = False
        elif isinstance(rs, str) ^ isinstance(cs, str):
            fp_match = False

    metric_abs_path = Path(metric_abs).resolve()
    baseline_spell = _baseline_shadow_for_ouroboros(base, metric_abs)
    score_channel_contract = _analyze_score_channel_for_shadow(
        baseline_spell,
        metric_kind="fixture_score",
        metric_rel=metric_rel,
        metric_abs=metric_abs_path,
    )

    records: List[Dict[str, Any]] = []
    for prop in proposals:
        mutation_path = str(prop["path"])
        choice = prop["candidate"]
        family = str(prop["family"])
        pid = str(prop["proposal_id"])
        ord_idx = int(prop["ordering_index"])

        shadow = _build_shadow_spell_for_ouroboros_proposal(
            base,
            metric_abs=metric_abs,
            mutation_path=mutation_path,
            candidate=choice,
            allowlist=allowlist,
        )
        predicted = _predicted_capability_kinds_for_spell(shadow)
        im = classify_input_manifest(shadow)
        unresolved_risk = bool(im.get("summary", {}).get("unresolved_dynamic_present"))
        unresolved_notes = [str(u.get("note", "")) for u in (im.get("unresolved_dynamic") or [])[:5]]

        prev_val: Any
        try:
            pre_shadow = load_spell(base.source_path) if base.source_path.exists() else base
            pre_shadow.inputs = dict(base.inputs)
            pre_shadow.inputs["score_path"] = metric_abs
            pre_shadow.graph = [Step(**step.__dict__) for step in base.graph]
            prev_val = _get_mutation_value(pre_shadow, mutation_path)
        except SpellValidationError:
            prev_val = None
        noop_detected = json.dumps(prev_val, sort_keys=True) == json.dumps(choice, sort_keys=True)

        reasons: List[str] = []
        review_bundle_compatibility = "not_applicable"
        capability_envelope_compatibility = "not_applicable"
        status = "admissible"

        if reviewed_bundle is not None:
            review_bundle_compatibility = "compatible" if fp_match else "unknown"
            if not fp_match:
                reasons.append("spell fingerprint does not match reviewed bundle required spell_sha256")
                status = "uncertain"

            if not envelope_present:
                capability_envelope_compatibility = "unknown"
                reasons.append("reviewed bundle has no capability envelope kinds")
                if status != "inadmissible":
                    status = "uncertain"
            else:
                assert reviewed_kinds is not None
                overreach = sorted(set(predicted) - reviewed_kinds)
                if overreach:
                    capability_envelope_compatibility = "incompatible"
                    status = "inadmissible"
                    reasons.append(
                        f"predicted capabilities not in reviewed envelope: {overreach}"
                    )
                else:
                    capability_envelope_compatibility = "compatible"

        if unresolved_risk and status != "inadmissible":
            status = "uncertain"
            if unresolved_notes:
                reasons.append(f"unresolved dynamic inputs: {unresolved_notes[0]}")

        if reviewed_bundle is None:
            review_bundle_compatibility = "not_applicable"
            capability_envelope_compatibility = "not_applicable"
            if unresolved_risk:
                status = "uncertain"
                if unresolved_notes:
                    reasons.append(f"unresolved dynamic inputs: {unresolved_notes[0]}")

        effect_sig = _effect_signature_canonical(shadow, mutation_path)
        effect_sig_id = _effect_signature_id(effect_sig)

        proposal_sc = _analyze_score_channel_for_shadow(
            shadow,
            metric_kind="fixture_score",
            metric_rel=metric_rel,
            metric_abs=metric_abs_path,
        )
        cb, cb_codes = _score_channel_clear_break(score_channel_contract, proposal_sc)
        sc_diff = _score_channel_diff_payload(score_channel_contract, proposal_sc, mutation_path)
        if (
            score_channel_contract.get("score_channel_status") == "aligned"
            and proposal_sc.get("score_channel_status") == "aligned"
        ):
            sc_preserved: Optional[bool] = list(score_channel_contract.get("writers_to_metric") or []) == list(
                proposal_sc.get("writers_to_metric") or []
            )
        else:
            sc_preserved = None

        sc_reasons = list(proposal_sc.get("score_channel_reasons") or [])
        sc_clear_break = False
        if block_score_channel_sensitive_mutations and _mutation_matches_score_channel_sensitive(
            mutation_path, sens_paths
        ):
            if status != "inadmissible":
                status = "inadmissible"
                reasons.append("score_channel_sensitive_mutation_blocked")
                sc_reasons.append("score_channel_sensitive_mutation_blocked")
        elif cb:
            sc_clear_break = True
            if status != "inadmissible":
                status = "inadmissible"
                for c in cb_codes:
                    reasons.append(c)
                    sc_reasons.append(c)
        elif proposal_sc.get("score_channel_status") == "uncertain" and status == "admissible":
            status = "uncertain"
            for x in sc_reasons:
                reasons.append(f"score_channel:{x}")
        elif proposal_sc.get("score_channel_status") == "not_aligned" and status == "admissible":
            status = "uncertain"
            reasons.append("score_channel:not_aligned_without_clear_break")
            sc_reasons.append("not_aligned_without_clear_break")

        rec = {
            "proposal_id": pid,
            "mutation_family": family,
            "mutation_target": mutation_path,
            "ordering_index": ord_idx,
            "candidate": choice,
            "admissibility_status": status,
            "reasons": reasons,
            "predicted_capabilities": predicted,
            "review_bundle_compatibility": review_bundle_compatibility,
            "capability_envelope_compatibility": capability_envelope_compatibility,
            "unresolved_input_risk": unresolved_risk,
            "noop_risk": noop_detected,
            "reviewed_bundle_enforce": bool(enforce_review_bundle),
            "effect_signature": effect_sig,
            "effect_signature_id": effect_sig_id,
            "score_channel_status": proposal_sc.get("score_channel_status"),
            "score_channel_baseline_status": score_channel_contract.get("score_channel_status"),
            "score_channel_diff": sc_diff,
            "score_channel_preserved": sc_preserved,
            "score_channel_reasons": sc_reasons,
            "score_channel_clear_break": sc_clear_break,
        }
        records.append(rec)

    ranked, diversification_summary = _finalize_diversified_ranking(records)
    skipped_ids = sorted({str(r["proposal_id"]) for r in records if r["admissibility_status"] == "inadmissible"})
    counts = {
        "admissible": sum(1 for r in records if r["admissibility_status"] == "admissible"),
        "uncertain": sum(1 for r in records if r["admissibility_status"] == "uncertain"),
        "inadmissible": sum(1 for r in records if r["admissibility_status"] == "inadmissible"),
    }
    sc_by_status = {"aligned": 0, "uncertain": 0, "not_aligned": 0}
    for r in records:
        st = str(r.get("score_channel_status") or "")
        if st in sc_by_status:
            sc_by_status[st] += 1
    score_channel_summary = {
        "by_status": sc_by_status,
        "clear_break_inadmissible": sum(1 for r in records if r.get("score_channel_clear_break")),
        "sensitive_blocked": sum(
            1 for r in records if "score_channel_sensitive_mutation_blocked" in (r.get("score_channel_reasons") or [])
        ),
    }
    summary = {
        "review_bundle_present": reviewed_bundle is not None,
        "envelope_present": envelope_present,
        "enforce_review_bundle": bool(enforce_review_bundle),
        "fingerprint_match": fp_match,
        **counts,
    }
    return {
        "proposal_plan_version": "1.5.0",
        "axiomurgy_version": VERSION,
        "total_proposals": len(records),
        "counts": counts,
        "proposals": records,
        "ranked_proposals": ranked,
        "skipped_inadmissible_proposal_ids": skipped_ids,
        "review_awareness_summary": summary,
        "diversification_summary": diversification_summary,
        "score_channel_contract": score_channel_contract,
        "score_channel_summary": score_channel_summary,
    }


def write_ouroboros_proposal_plan(
    artifact_dir: Path,
    spell_name: str,
    plan_doc: Dict[str, Any],
) -> Tuple[Path, Path]:
    raw_path = artifact_dir / f"{spell_name}.proposal_plan.raw.json"
    diff_path = artifact_dir / f"{spell_name}.proposal_plan.json"
    raw_path.write_text(canonical_json(plan_doc), encoding="utf-8")
    diff_path.write_text(
        canonical_json(
            normalize_paths_for_portability(json.loads(canonical_json(plan_doc)), repo_root=ROOT)
        ),
        encoding="utf-8",
    )
    return diff_path, raw_path


def _replay_execution_subset_for_compare(exec_result: Dict[str, Any]) -> Dict[str, Any]:
    caps = exec_result.get("capabilities") or {}
    fp = exec_result.get("fingerprints") or {}
    return {
        "status": exec_result.get("status"),
        "fingerprints_required": _json_safe_replay(fp.get("required")),
        "capabilities": {
            "used": sorted(caps.get("used") or []),
            "overreach": sorted(caps.get("overreach") or []),
            "reviewed_envelope": caps.get("reviewed_envelope"),
        },
        "blocked": _json_safe_replay(exec_result.get("blocked")) if exec_result.get("blocked") is not None else None,
    }


def replay_ouroboros_revolution(
    resolved: ResolvedRunTarget,
    *,
    revolution_dir: Path,
    approvals: Set[str],
    simulate: bool,
    reviewed_bundle: Optional[Dict[str, Any]],
    enforce_review_bundle: bool,
    replay_artifact_root: Path,
) -> Dict[str, Any]:
    """Re-execute a stored veil revolution and compare to replay_record.json (v2.0)."""
    base_out: Dict[str, Any] = {
        "mode": "replay",
        "axiomurgy_version": VERSION,
        "original_revolution_id": "",
        "source_run_id": "",
        "source_revolution_dir": str(revolution_dir.resolve()),
        "compared_fields": [],
        "mismatch_reasons": [],
        "replay_summary_path": None,
        "replay_summary_raw_path": None,
    }

    def _nr(reasons: List[str]) -> Dict[str, Any]:
        out = dict(base_out)
        out["replay_status"] = "non_replayable"
        out["mismatch_reasons"] = reasons
        return out

    if resolved.spellbook is not None:
        return _nr(["spellbook_replay_not_supported"])

    rr_path = revolution_dir.resolve() / "replay_record.json"
    if not rr_path.is_file():
        return _nr(["missing_replay_record"])

    record = load_json(rr_path)
    base_out["original_revolution_id"] = str(record.get("revolution_id") or "")
    base_out["source_run_id"] = str(record.get("run_id") or "")

    req_fp = compute_spell_fingerprints(resolved.spell, resolved.policy_path, repo_root=ROOT).get("required")
    if canonical_json(_json_safe_replay(req_fp or {})) != canonical_json(_json_safe_replay(record.get("spell_fingerprints_required") or {})):
        return _nr(["spell_fingerprint_mismatch"])

    if str(resolved.policy_path.resolve()) != str(record.get("policy_path") or ""):
        return _nr(["policy_path_mismatch"])

    rb_fp_exp = record.get("review_bundle_fingerprint")
    ra = record.get("recorded_attestation")
    if ra is not None:
        if reviewed_bundle is None:
            return _nr(["review_bundle_required_for_replay"])
        if _review_bundle_fingerprint(reviewed_bundle) != rb_fp_exp:
            return _nr(["review_bundle_fingerprint_mismatch"])

    replay_artifact_root.mkdir(parents=True, exist_ok=True)
    replay_run = replay_artifact_root / "replay_run"
    if replay_run.exists():
        shutil.rmtree(replay_run)
    replay_run.mkdir(parents=True, exist_ok=True)
    for mp, content in sorted((record.get("metric_files_before_veil") or {}).items()):
        p = replay_run / mp
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = content if isinstance(content, dict) else {}
        p.write_text(canonical_json(doc), encoding="utf-8")

    shadow_path = revolution_dir.resolve() / "shadow.spell.json"
    sh = load_spell(shadow_path)
    metric_path = str(record["metric_path"])
    metric_abs = str((replay_run / metric_path).resolve())
    sh.inputs = dict(sh.inputs)
    sh.inputs["score_path"] = metric_abs

    exec_dir = replay_artifact_root / "replay_exec"
    if exec_dir.exists():
        shutil.rmtree(exec_dir)
    exec_dir.mkdir(parents=True, exist_ok=True)

    exec_new = execute_spell(
        sh,
        ["approve", "read", "reason", "simulate", "transform", "verify", "write"],
        approvals,
        simulate,
        resolved.policy_path,
        exec_dir,
        reviewed_bundle=reviewed_bundle,
        enforce_review_bundle=enforce_review_bundle,
    )

    score_new = _fixture_score_safe(replay_run, metric_path)
    exec_ok = exec_new.get("status") == "succeeded"
    si = record["seal_inputs"]
    seal_new = evaluate_acceptance_contract(
        artifact_dir=replay_run,
        contract=si["acceptance_contract"],
        execution_succeeded=exec_ok,
        candidate_primary=float(score_new),
        best_primary=float(si["best_primary_at_seal"]),
        initial_metrics={k: float(v) for k, v in sorted(si["initial_metrics"].items())},
        metrics_at_best={k: float(v) for k, v in sorted(si["metrics_at_best"].items())},
        metrics_at_last_accept={k: float(v) for k, v in sorted(si["metrics_at_last_accept"].items())},
        rec=si["rec"],
        last_accepted_rec=si.get("last_accepted_rec"),
        best_ordering_index=si.get("best_ordering_index"),
        candidate_ordering_index=int(si["candidate_ordering_index"]),
        revolution=int(si["revolution"]),
        last_accepted_revolution=si.get("last_accepted_revolution"),
    )
    _enrich_seal_baseline_reference_ids(
        seal_new,
        initial_baseline_id=str(si["initial_baseline_id"]),
        active_baseline_id=str(si["active_baseline_id"]),
        last_accepted_baseline_id=si.get("last_accepted_baseline_id"),
    )

    compared_fields: List[str] = []
    mismatch_reasons: List[str] = []

    compared_fields.append("primary_score")
    if not _scores_equal(float(score_new), float(record["recorded_score_after"])):
        mismatch_reasons.append("score_mismatch")

    compared_fields.append("seal_decision")
    if canonical_json(_json_safe_replay(seal_new)) != canonical_json(_json_safe_replay(record.get("recorded_seal_decision") or {})):
        mismatch_reasons.append("seal_decision_mismatch")

    compared_fields.append("execution")
    rec_ex = record.get("recorded_execution") or {}
    new_ex = _replay_execution_subset_for_compare(exec_new)
    if canonical_json(_json_safe_replay(rec_ex)) != canonical_json(_json_safe_replay(new_ex)):
        mismatch_reasons.append("execution_mismatch")

    if ra is not None:
        compared_fields.append("attestation")
        att_new = compute_attestation(reviewed_bundle, resolved, approvals=approvals)
        new_ra = {
            "status": att_new["status"],
            "diff_paths": sorted(
                str(d["path"])
                for d in (att_new.get("diffs") or [])
                if isinstance(d, dict) and d.get("path") is not None
            ),
        }
        if canonical_json(_json_safe_replay(new_ra)) != canonical_json(_json_safe_replay(ra)):
            mismatch_reasons.append("attestation_mismatch")

    replay_status = "match" if not mismatch_reasons else "drift"

    summary_raw: Dict[str, Any] = {
        "mode": "replay",
        "replay_status": replay_status,
        "original_revolution_id": base_out["original_revolution_id"],
        "source_run_id": base_out["source_run_id"],
        "source_revolution_dir": base_out["source_revolution_dir"],
        "replay_record_path": str(rr_path),
        "compared_fields": compared_fields,
        "mismatch_reasons": mismatch_reasons,
        "replay_run_root": str(replay_run.resolve()),
        "replay_exec_trace": str((exec_dir / f"{sh.name}.trace.json").resolve()),
    }
    raw_path = replay_artifact_root / "replay_summary.raw.json"
    diff_path = replay_artifact_root / "replay_summary.json"
    raw_path.write_text(canonical_json(summary_raw), encoding="utf-8")
    diff_path.write_text(
        canonical_json(
            normalize_paths_for_portability(json.loads(canonical_json(summary_raw)), repo_root=ROOT)
        ),
        encoding="utf-8",
    )

    out = dict(base_out)
    out["replay_status"] = replay_status
    out["compared_fields"] = compared_fields
    out["mismatch_reasons"] = mismatch_reasons
    out["replay_summary_path"] = str(diff_path.resolve())
    out["replay_summary_raw_path"] = str(raw_path.resolve())
    out["score_replay"] = float(score_new)
    out["score_recorded"] = float(record["recorded_score_after"])
    return out


def ouroboros_chamber(
    resolved: ResolvedRunTarget,
    *,
    cycle_config_path: Path,
    approvals: Set[str],
    simulate: bool,
    reviewed_bundle: Optional[Dict[str, Any]],
    enforce_review_bundle: bool,
) -> Dict[str, Any]:
    cfg = load_cycle_config(cycle_config_path)
    run_capsule_cfg = cfg["run_capsule"]
    base_artifact_dir = resolved.artifact_dir
    if run_capsule_cfg["enabled"]:
        run_id, art, started_logical_index = _allocate_ouroboros_run_capsule(base_artifact_dir)
    else:
        run_id = "legacy_flat"
        art = base_artifact_dir
        started_logical_index = 0
    cycle_config_fingerprint = sha256_bytes(canonical_json(cfg).encode("utf-8"))
    spell_fp = compute_spell_fingerprints(resolved.spell, resolved.policy_path, repo_root=ROOT)
    spellbook_fp = compute_spellbook_fingerprints(resolved, repo_root=ROOT) if resolved.spellbook else {}
    review_bundle_fp = _review_bundle_fingerprint(reviewed_bundle)
    run_capsule_meta: Dict[str, Any] = {
        "run_id": run_id,
        "run_version": VERSION,
        "started_at_logical_index": started_logical_index,
        "artifact_root": str(art.resolve()),
        "cycle_config_fingerprint": cycle_config_fingerprint,
        "spell_fingerprints_required": spell_fp.get("required"),
        "spellbook_fingerprints_required": (spellbook_fp.get("required") if spellbook_fp else None),
        "review_bundle_fingerprint": review_bundle_fp,
        "mode": "cycle",
    }

    allowlist = cfg["mutation_target_allowlist"]
    max_rev = cfg["max_revolutions"]
    flux_budget = cfg["flux_budget"]
    plateau_window = cfg["plateau_window"]
    stop = cfg["stop_conditions"]
    metric_path = cfg["target_metric"]["path"]
    metric_abs = str((art / metric_path).resolve())
    recall_cfg = cfg["recall"]
    k_succ = recall_cfg["recent_k_successes"]
    k_fail = recall_cfg["recent_k_failures"]
    reject_on_noop = cfg["reject_on_noop"]
    acc_contract = cfg["acceptance_contract"]

    proposals = expand_cycle_proposals(cfg)
    if not proposals:
        raise SpellValidationError("cycle config produced no proposals")

    chamber_dir = art / "ouroboros"
    chamber_dir.mkdir(parents=True, exist_ok=True)
    # Isolate this run from leftover shadow spells (stale rev_*.spell.json confuses audits and disk usage).
    for stale in sorted(chamber_dir.glob("rev_*.spell.json")):
        try:
            stale.unlink()
        except OSError:
            pass

    revolutions: List[Dict[str, Any]] = []
    failures = 0
    best_score = float("-inf")
    best_spell = resolved.spell
    best_ordering_index: Optional[int] = None
    no_improve = 0
    attempted = 0
    rejected_ids: Set[str] = set()

    recent_successes: deque = deque(maxlen=k_succ) if k_succ > 0 else deque()
    recent_failures: deque = deque(maxlen=k_fail) if k_fail > 0 else deque()

    accepted_mutation_count = 0
    rejected_mutation_count = 0

    # baseline execute to establish score (guardrails: failures => -inf)
    baseline_spell = load_spell(resolved.spell.source_path)
    baseline_spell.inputs = dict(resolved.spell.inputs)
    baseline_spell.inputs["score_path"] = metric_abs
    baseline_result = execute_spell(
        baseline_spell,
        ["approve", "read", "reason", "simulate", "transform", "verify", "write"],
        approvals,
        simulate,
        resolved.policy_path,
        art,
        reviewed_bundle=reviewed_bundle,
        enforce_review_bundle=enforce_review_bundle,
    )
    baseline_score = float("-inf")
    if baseline_result.get("status") == "succeeded":
        try:
            baseline_score = _fixture_score(art, metric_path)
        except Exception:
            baseline_score = float("-inf")
    best_score = baseline_score
    best_ordering_index = None

    metric_paths_set: Set[str] = {metric_path}
    for g in acc_contract.get("guardrails") or []:
        metric_paths_set.add(str(g["metric_path"]))
    initial_metrics: Dict[str, float] = {
        p: _fixture_score_safe(art, p) for p in sorted(metric_paths_set)
    }
    metrics_at_best: Dict[str, float] = dict(initial_metrics)
    metrics_at_last_accept: Dict[str, float] = dict(initial_metrics)
    last_accepted_rec: Optional[Dict[str, Any]] = None
    last_accepted_revolution: Optional[int] = None
    acceptance_summary: Dict[str, int] = {
        "accepted_by_contract": 0,
        "rejected_by_contract": 0,
        "rejected_by_guardrail": 0,
        "rejected_by_tiebreak": 0,
        "rejected_by_score_channel": 0,
        "rejected_by_reject_if_non_score": 0,
    }

    lineage_policy = cfg.get("lineage_policy") or {}
    record_rejected_snapshots = bool(lineage_policy.get("record_rejected_snapshots", True))

    baseline_seq = 0
    promotion_seq = 0
    baseline_registry: List[Dict[str, Any]] = []
    promotion_records: List[Dict[str, Any]] = []
    baseline_seq += 1
    initial_baseline_id = _format_baseline_id(baseline_seq)
    active_baseline_id = initial_baseline_id
    last_accepted_baseline_id: Optional[str] = None

    baseline_registry.append(
        {
            "baseline_id": initial_baseline_id,
            "parent_baseline_id": None,
            "created_at_logical_index": baseline_seq,
            "source_revolution": 0,
            "source_proposal_id": None,
            "primary_metric_path": metric_path,
            "primary_metric_value": float(baseline_score),
            "guardrail_snapshot": _guardrail_metrics_snapshot(initial_metrics, metric_paths_set),
            "admissibility_snapshot": None,
            "score_channel_snapshot": None,
            "status": "initial",
        }
    )

    plan_doc = plan_ouroboros_proposals(
        resolved,
        proposals=proposals,
        allowlist=allowlist,
        metric_abs=metric_abs,
        metric_rel=metric_path,
        reviewed_bundle=reviewed_bundle,
        enforce_review_bundle=enforce_review_bundle,
        score_channel_sensitive_paths=cfg.get("score_channel_sensitive_paths") or [],
        block_score_channel_sensitive_mutations=bool(cfg.get("block_score_channel_sensitive_mutations", False)),
    )
    proposal_plan_diff_path, proposal_plan_raw_path = write_ouroboros_proposal_plan(
        art,
        resolved.spell.name,
        plan_doc,
    )
    ranked_list: List[Dict[str, Any]] = list(plan_doc["ranked_proposals"])
    preflight_skips: List[Dict[str, Any]] = []
    revolution_capsules: List[Dict[str, Any]] = []
    proposal_id_to_revolution_id: Dict[str, str] = {}
    capsule_seq = 0
    revolution_retention = str(run_capsule_cfg.get("revolution_retention") or "preserve_all")

    stop_reason = None
    prop_idx = 0
    revolution = 0

    while revolution < max_rev:
        if attempted >= flux_budget:
            stop_reason = "flux_budget"
            break
        if failures >= stop["max_failures"]:
            stop_reason = "max_failures"
            break
        if no_improve >= stop["no_improve_for"]:
            stop_reason = "plateau"
            break

        # Linear scan over ranked proposals (v1.4 diversified order): skip rejected_ids and inadmissible preflight.
        n_props = len(ranked_list)
        if n_props == 0:
            stop_reason = "exhausted_candidates"
            break
        while prop_idx < n_props:
            rec = ranked_list[prop_idx]
            pid = str(rec["proposal_id"])
            if pid in rejected_ids:
                prop_idx += 1
                continue
            if rec.get("admissibility_status") == "inadmissible":
                skip_reason = "inadmissible_preflight"
                if rec.get("score_channel_clear_break"):
                    skip_reason = "score_channel_clear_break"
                elif "score_channel_sensitive_mutation_blocked" in (rec.get("reasons") or []):
                    skip_reason = "score_channel_sensitive_blocked"
                preflight_skips.append(
                    {
                        "proposal_id": pid,
                        "mutation_family": rec.get("mutation_family"),
                        "mutation_target": rec.get("mutation_target"),
                        "skip_reason": skip_reason,
                        "admissibility_status": "inadmissible",
                        "reasons": list(rec.get("reasons") or []),
                        "score_channel_clear_break": bool(rec.get("score_channel_clear_break")),
                        "score_channel_status": rec.get("score_channel_status"),
                    }
                )
                capsule_seq += 1
                preflight_rid = _format_revolution_capsule_id(capsule_seq)
                proposal_id_to_revolution_id[pid] = preflight_rid
                revolution_capsules.append(
                    {
                        "revolution_id": preflight_rid,
                        "revolution_index": capsule_seq,
                        "parent_run_id": run_id,
                        "artifact_root_relative": None,
                        "proposal_id": pid,
                        "mutation_family": rec.get("mutation_family"),
                        "mutation_target": rec.get("mutation_target"),
                        "executed": False,
                        "skipped_reason": skip_reason,
                    }
                )
                prop_idx += 1
                continue
            break
        if prop_idx >= n_props:
            stop_reason = "exhausted_candidates"
            break

        rec = ranked_list[prop_idx]
        prop_idx += 1
        revolution += 1
        mutation_path = str(rec["mutation_target"])
        choice = rec["candidate"]
        family = str(rec["mutation_family"])
        pid = str(rec["proposal_id"])
        ord_idx = int(rec["ordering_index"])
        capsule_seq += 1
        veil_revolution_id = _format_revolution_capsule_id(capsule_seq)
        proposal_id_to_revolution_id[pid] = veil_revolution_id

        recall_snapshot = _ouroboros_recall_snapshot(
            recent_successes=recent_successes,
            recent_failures=recent_failures,
            best_score_so_far=best_score,
            current_plateau_length=no_improve,
            accepted_mutation_count=accepted_mutation_count,
            rejected_mutation_count=rejected_mutation_count,
        )

        state_trace = ["recall", "commune", "forge", "veil", "seal"]

        shadow_spell = load_spell(best_spell.source_path) if best_spell.source_path.exists() else best_spell
        shadow_spell.inputs = dict(best_spell.inputs)
        shadow_spell.inputs["score_path"] = metric_abs
        shadow_spell.graph = [Step(**step.__dict__) for step in best_spell.graph]

        previous_value: Any
        try:
            previous_value = _get_mutation_value(shadow_spell, mutation_path)
        except SpellValidationError:
            previous_value = None

        noop = json.dumps(previous_value, sort_keys=True) == json.dumps(choice, sort_keys=True)
        exec_result: Dict[str, Any] = {}
        score = float("-inf")

        if reject_on_noop and noop:
            accept_reject_reason = "noop"
            accepted = False
            rejected_ids.add(pid)
            rejected_mutation_count += 1
            no_improve += 1
            revolution_capsules.append(
                {
                    "revolution_id": veil_revolution_id,
                    "revolution_index": capsule_seq,
                    "parent_run_id": run_id,
                    "artifact_root_relative": None,
                    "proposal_id": pid,
                    "mutation_family": family,
                    "mutation_target": mutation_path,
                    "executed": False,
                    "skipped_reason": "noop",
                }
            )
            if k_fail > 0:
                recent_failures.append(
                    {
                        "proposal_id": pid,
                        "score": best_score,
                        "mutation_family": family,
                        "path": mutation_path,
                        "accepted": False,
                    }
                )
            revolutions.append(
                {
                    "revolution": revolution,
                    "revolution_id": veil_revolution_id,
                    "artifact_root_relative": None,
                    "states": state_trace,
                    "mutation_family": family,
                    "mutation_target": mutation_path,
                    "proposal_id": pid,
                    "mutation_fingerprint": pid,
                    "proposed_value": choice,
                    "previous_value": previous_value,
                    "recall_snapshot": recall_snapshot,
                    "score_before": best_score,
                    "score_after": best_score,
                    "baseline_score": baseline_score,
                    "accept_reject_reason": accept_reject_reason,
                    "mutation": {"path": mutation_path, "value": choice},
                    "candidate_score": score,
                    "accepted": accepted,
                    "rejected": True,
                    "rollback": True,
                    "stop_reason": None,
                    "execution_result": {
                        "status": "skipped",
                        "trace_path": None,
                        "proof_path": None,
                        "capability_denials": None,
                    },
                    "seal_decision": None,
                    "active_baseline_id": active_baseline_id,
                }
            )
            continue

        from_baseline_id = active_baseline_id
        metrics_lineage_before = _guardrail_metrics_snapshot(metrics_at_best, metric_paths_set)

        _set_mutation(shadow_spell, mutation_path, choice, allowlist)

        shadow_path = chamber_dir / f"rev_{revolution:03d}.spell.json"

        def step_to_json(s: Step, *, is_rollback: bool) -> Dict[str, Any]:
            out: Dict[str, Any] = {
                "id": s.step_id,
                "rune": s.rune,
                "effect": s.effect,
                "args": s.args,
                "requires": s.requires,
                "description": s.description,
            }
            if s.output_schema is not None:
                out["output_schema"] = s.output_schema
            if s.confidence is not None:
                out["confidence"] = s.confidence
            if is_rollback:
                out["compensates"] = s.compensates
            return out

        shadow_raw = {
            "spell": shadow_spell.name,
            "intent": shadow_spell.intent,
            "inputs": shadow_spell.inputs,
            "constraints": shadow_spell.constraints,
            "graph": [step_to_json(s, is_rollback=False) for s in shadow_spell.graph],
            "rollback": [step_to_json(s, is_rollback=True) for s in shadow_spell.rollback],
            "witness": shadow_spell.witness,
        }
        shadow_path.write_text(json.dumps(shadow_raw, indent=2, ensure_ascii=False), encoding="utf-8")

        rev_rel = f"revolutions/{veil_revolution_id}"
        rev_art = art / "revolutions" / veil_revolution_id
        rev_art.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shadow_path, rev_art / "shadow.spell.json")

        metric_files_before_veil = _snapshot_metric_json_for_replay(art, metric_paths_set)

        score_before = best_score
        attempted += 1
        exec_result = execute_spell(
            load_spell(shadow_path),
            ["approve", "read", "reason", "simulate", "transform", "verify", "write"],
            approvals,
            simulate,
            resolved.policy_path,
            rev_art,
            reviewed_bundle=reviewed_bundle,
            enforce_review_bundle=enforce_review_bundle,
        )
        revolution_capsules.append(
            {
                "revolution_id": veil_revolution_id,
                "revolution_index": capsule_seq,
                "parent_run_id": run_id,
                "artifact_root_relative": rev_rel,
                "proposal_id": pid,
                "mutation_family": family,
                "mutation_target": mutation_path,
                "executed": True,
                "skipped_reason": None,
            }
        )
        if exec_result.get("status") == "succeeded":
            try:
                score = _fixture_score(art, metric_path)
            except Exception:
                score = float("-inf")
        else:
            score = float("-inf")

        exec_ok = exec_result.get("status") == "succeeded"
        seal = evaluate_acceptance_contract(
            artifact_dir=art,
            contract=acc_contract,
            execution_succeeded=exec_ok,
            candidate_primary=score,
            best_primary=best_score,
            initial_metrics=initial_metrics,
            metrics_at_best=metrics_at_best,
            metrics_at_last_accept=metrics_at_last_accept,
            rec=rec,
            last_accepted_rec=last_accepted_rec,
            best_ordering_index=best_ordering_index,
            candidate_ordering_index=ord_idx,
            revolution=revolution,
            last_accepted_revolution=last_accepted_revolution,
        )
        contract_accept_reason = (seal.get("reasons") or ["contract_unknown"])[0]
        _enrich_seal_baseline_reference_ids(
            seal,
            initial_baseline_id=initial_baseline_id,
            active_baseline_id=active_baseline_id,
            last_accepted_baseline_id=last_accepted_baseline_id,
        )
        _write_ouroboros_replay_record(
            rev_art,
            run_id=run_id,
            revolution_id=veil_revolution_id,
            cycle_config_fingerprint=cycle_config_fingerprint,
            metric_path=metric_path,
            spell_fingerprints_required=spell_fp.get("required"),
            resolved=resolved,
            approvals=approvals,
            simulate=simulate,
            enforce_review_bundle=enforce_review_bundle,
            reviewed_bundle=reviewed_bundle,
            initial_metrics=initial_metrics,
            metrics_at_best=metrics_at_best,
            metrics_at_last_accept=metrics_at_last_accept,
            rec=rec,
            last_accepted_rec=last_accepted_rec,
            best_ordering_index=best_ordering_index,
            candidate_ordering_index=ord_idx,
            revolution=revolution,
            last_accepted_revolution=last_accepted_revolution,
            acc_contract=acc_contract,
            initial_baseline_id=initial_baseline_id,
            active_baseline_id=active_baseline_id,
            last_accepted_baseline_id=last_accepted_baseline_id,
            best_primary_at_seal=float(best_score),
            metric_files_before_veil=metric_files_before_veil,
            exec_result=exec_result,
            seal=seal,
            score_after=float(score),
        )
        _record_seal_acceptance_summary(acceptance_summary, seal)
        accepted = seal.get("decision") == "accept"
        accept_reject_reason = contract_accept_reason
        if accepted and cfg["require_approval_for_accept"] and "accept" not in approvals:
            accepted = False
            accept_reject_reason = "approval_required"

        if accepted:
            old_active = active_baseline_id
            for br in baseline_registry:
                if br["baseline_id"] == old_active and br["status"] in ("initial", "active"):
                    br["status"] = "superseded"
                    break
            best_score = score
            best_spell = load_spell(shadow_path)
            best_ordering_index = ord_idx
            no_improve = 0
            accepted_mutation_count += 1
            snap_m = {p: _fixture_score_safe(art, p) for p in metric_paths_set}
            metrics_at_best = snap_m
            metrics_at_last_accept = dict(snap_m)
            baseline_seq += 1
            new_baseline_id = _format_baseline_id(baseline_seq)
            gr_snap = _guardrail_metrics_snapshot(snap_m, metric_paths_set)
            baseline_registry.append(
                {
                    "baseline_id": new_baseline_id,
                    "parent_baseline_id": old_active,
                    "created_at_logical_index": baseline_seq,
                    "source_revolution": revolution,
                    "source_proposal_id": pid,
                    "primary_metric_path": metric_path,
                    "primary_metric_value": float(score),
                    "guardrail_snapshot": gr_snap,
                    "admissibility_snapshot": {"admissibility_status": rec.get("admissibility_status")},
                    "score_channel_snapshot": {"score_channel_status": rec.get("score_channel_status")},
                    "status": "active",
                }
            )
            promotion_seq += 1
            promotion_records.append(
                {
                    "promotion_id": _format_promotion_id(promotion_seq),
                    "from_baseline_id": from_baseline_id,
                    "to_baseline_id": new_baseline_id,
                    "proposal_id": pid,
                    "promotion_reason": contract_accept_reason,
                    "acceptance_decision_summary": _slim_seal_for_promotion(seal),
                    "metrics_before": dict(sorted(metrics_lineage_before.items())),
                    "metrics_after": dict(sorted(snap_m.items())),
                    "guardrails_before": _guardrail_metrics_snapshot(metrics_lineage_before, metric_paths_set),
                    "guardrails_after": gr_snap,
                }
            )
            active_baseline_id = new_baseline_id
            last_accepted_baseline_id = new_baseline_id
            last_accepted_rec = rec
            last_accepted_revolution = revolution
            if k_succ > 0:
                recent_successes.append(
                    {
                        "proposal_id": pid,
                        "score": score,
                        "mutation_family": family,
                        "path": mutation_path,
                        "accepted": True,
                    }
                )
        else:
            no_improve += 1
            rejected_mutation_count += 1
            if accept_reject_reason != "approval_required":
                rejected_ids.add(pid)
            if (
                record_rejected_snapshots
                and exec_ok
                and accept_reject_reason != "approval_required"
            ):
                cand_snap = {p: _fixture_score_safe(art, p) for p in metric_paths_set}
                baseline_seq += 1
                rej_snap_id = _format_baseline_id(baseline_seq)
                baseline_registry.append(
                    {
                        "baseline_id": rej_snap_id,
                        "parent_baseline_id": from_baseline_id,
                        "created_at_logical_index": baseline_seq,
                        "source_revolution": revolution,
                        "source_proposal_id": pid,
                        "primary_metric_path": metric_path,
                        "primary_metric_value": float(score),
                        "guardrail_snapshot": _guardrail_metrics_snapshot(cand_snap, metric_paths_set),
                        "admissibility_snapshot": {"admissibility_status": rec.get("admissibility_status")},
                        "score_channel_snapshot": {"score_channel_status": rec.get("score_channel_status")},
                        "status": "rejected_candidate_snapshot",
                    }
                )
            if k_fail > 0:
                recent_failures.append(
                    {
                        "proposal_id": pid,
                        "score": score,
                        "mutation_family": family,
                        "path": mutation_path,
                        "accepted": False,
                    }
                )
            if exec_result.get("status") != "succeeded":
                failures += 1

        revolutions.append(
            {
                "revolution": revolution,
                "revolution_id": veil_revolution_id,
                "artifact_root_relative": rev_rel,
                "states": state_trace,
                "mutation_family": family,
                "mutation_target": mutation_path,
                "proposal_id": pid,
                "mutation_fingerprint": pid,
                "proposed_value": choice,
                "previous_value": previous_value,
                "recall_snapshot": recall_snapshot,
                "score_before": score_before,
                "score_after": score,
                "baseline_score": baseline_score,
                "accept_reject_reason": accept_reject_reason,
                "mutation": {"path": mutation_path, "value": choice},
                "candidate_score": score,
                "accepted": accepted,
                "rejected": not accepted,
                "rollback": (not accepted),
                "stop_reason": None,
                "execution_result": {
                    "status": exec_result.get("status"),
                    "trace_path": exec_result.get("trace_path"),
                    "proof_path": exec_result.get("proof_path"),
                    "capability_denials": exec_result.get("blocked"),
                },
                "seal_decision": seal,
                "active_baseline_id": active_baseline_id,
            }
        )

    if stop_reason is None:
        stop_reason = "max_revolutions"

    recall_final = _ouroboros_recall_snapshot(
        recent_successes=recent_successes,
        recent_failures=recent_failures,
        best_score_so_far=best_score,
        current_plateau_length=no_improve,
        accepted_mutation_count=accepted_mutation_count,
        rejected_mutation_count=rejected_mutation_count,
    )

    lineage_summary = _lineage_summary_top(baseline_registry, promotion_records, active_baseline_id)

    revolution_count_total = len(revolution_capsules)
    revolution_count_executed = sum(1 for c in revolution_capsules if c.get("executed"))
    revolution_count_skipped = revolution_count_total - revolution_count_executed
    revolution_artifact_roots = [c.get("artifact_root_relative") for c in revolution_capsules]

    run_capsule_meta["revolution_capsules"] = revolution_capsules
    run_capsule_meta["proposal_id_to_revolution_id"] = proposal_id_to_revolution_id
    run_capsule_meta["revolution_retention"] = revolution_retention
    run_capsule_meta["revolution_count_total"] = revolution_count_total
    run_capsule_meta["revolution_count_executed"] = revolution_count_executed
    run_capsule_meta["revolution_count_skipped"] = revolution_count_skipped
    run_capsule_meta["revolution_artifact_roots"] = revolution_artifact_roots

    sn = resolved.spell.name
    ouro_raw_path = art / f"{sn}.ouroboros.raw.json"
    ouro_diff_path = art / f"{sn}.ouroboros.json"
    key_paths_for_run: Dict[str, Path] = {
        "ouroboros_witness_raw": ouro_raw_path,
        "ouroboros_witness_diff": ouro_diff_path,
        "proposal_plan_raw": proposal_plan_raw_path,
        "proposal_plan_diff": proposal_plan_diff_path,
    }
    for cap in revolution_capsules:
        rel = cap.get("artifact_root_relative")
        if isinstance(rel, str) and rel:
            rid = str(cap.get("revolution_id") or "")
            if rid:
                key_paths_for_run[f"revolution_root_{rid}"] = art / rel
    key_artifact_paths_relative = _paths_relative_to_run_root(art, key_paths_for_run)

    witness = {
        "mode": "cycle",
        "chamber": "ouroboros",
        "config_path": str(cycle_config_path),
        "max_revolutions": max_rev,
        "flux_budget": flux_budget,
        "plateau_window": plateau_window,
        "stop_reason": stop_reason,
        "baseline_score": baseline_score,
        "best_score": best_score,
        "recall": recall_final,
        "revolutions": revolutions,
        "preflight_skips": preflight_skips,
        "flux_attempts": attempted,
        "proposal_plan_path": str(proposal_plan_diff_path),
        "proposal_plan_raw_path": str(proposal_plan_raw_path),
        "score_channel_contract": plan_doc.get("score_channel_contract"),
        "score_channel_summary": plan_doc.get("score_channel_summary"),
        "acceptance_contract": acc_contract,
        "acceptance_summary": acceptance_summary,
        "lineage_policy": lineage_policy,
        "baseline_registry": baseline_registry,
        "promotion_records": promotion_records,
        "lineage_summary": lineage_summary,
        "run_capsule": run_capsule_meta,
        "revolution_capsules": revolution_capsules,
        "proposal_id_to_revolution_id": proposal_id_to_revolution_id,
        "revolution_count_total": revolution_count_total,
        "revolution_count_executed": revolution_count_executed,
        "revolution_count_skipped": revolution_count_skipped,
        "revolution_artifact_roots": revolution_artifact_roots,
        "key_artifact_paths_relative": key_artifact_paths_relative,
        "nondeterministic_fields": [],
    }
    ouro_raw_path.write_text(canonical_json(witness), encoding="utf-8")
    ouro_diff_path.write_text(
        canonical_json(normalize_paths_for_portability(json.loads(canonical_json(witness)), repo_root=ROOT)),
        encoding="utf-8",
    )

    manifest_doc: Dict[str, Any] = {
        "run_capsule": run_capsule_meta,
        "base_artifact_dir": str(base_artifact_dir.resolve()),
        "stop_reason": stop_reason,
        "lineage_summary": lineage_summary,
        "acceptance_summary": acceptance_summary,
        "promotion_record_count": len(promotion_records),
        "final_active_baseline_id": lineage_summary["final_active_baseline_id"],
        "ouroboros_witness_path": str(ouro_diff_path),
        "ouroboros_witness_raw_path": str(ouro_raw_path),
        "proposal_plan_path": str(proposal_plan_diff_path),
        "proposal_plan_raw_path": str(proposal_plan_raw_path),
        "key_artifact_paths_relative": key_artifact_paths_relative,
        "run_manifest_path": str(art / f"{sn}.run_manifest.json"),
        "run_manifest_raw_path": str(art / f"{sn}.run_manifest.raw.json"),
        "revolution_capsules": revolution_capsules,
        "proposal_id_to_revolution_id": proposal_id_to_revolution_id,
        "revolution_count_total": revolution_count_total,
        "revolution_count_executed": revolution_count_executed,
        "revolution_count_skipped": revolution_count_skipped,
        "revolution_artifact_roots": revolution_artifact_roots,
    }
    run_manifest_diff_path, run_manifest_raw_path = write_ouroboros_run_manifest(art, sn, manifest_doc)
    _maybe_prune_old_run_capsules(base_artifact_dir, run_capsule_cfg)

    return {
        "mode": "cycle",
        "status": "completed",
        "stop_reason": stop_reason,
        "baseline_score": baseline_score,
        "best_score": best_score,
        "run_id": run_id,
        "run_artifact_root": str(art.resolve()),
        "base_artifact_dir": str(base_artifact_dir.resolve()),
        "ouroboros_witness_path": str(ouro_diff_path),
        "ouroboros_witness_raw_path": str(ouro_raw_path),
        "proposal_plan_path": str(proposal_plan_diff_path),
        "proposal_plan_raw_path": str(proposal_plan_raw_path),
        "run_manifest_path": str(run_manifest_diff_path),
        "run_manifest_raw_path": str(run_manifest_raw_path),
        "flux_attempts": attempted,
        "acceptance_contract": acc_contract,
        "acceptance_summary": acceptance_summary,
        "lineage_policy": lineage_policy,
        "baseline_registry": baseline_registry,
        "promotion_records": promotion_records,
        "lineage_summary": lineage_summary,
        "revolution_capsules": revolution_capsules,
        "proposal_id_to_revolution_id": proposal_id_to_revolution_id,
        "revolution_count_total": revolution_count_total,
        "revolution_count_executed": revolution_count_executed,
        "revolution_count_skipped": revolution_count_skipped,
        "revolution_artifact_roots": revolution_artifact_roots,
    }


def coerce_text(ctx: RuneContext, value: Any) -> str:
    if isinstance(value, str):
        if "\n" in value or len(value) > 240 or "://" in value:
            return value
        path = ctx.maybe_path(value)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return value
    if isinstance(value, Path):
        return value.read_text(encoding="utf-8")
    return json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)



def target_label(step: Step, args: Dict[str, Any], fallback: str) -> str:
    if isinstance(args.get("target_name"), str) and args.get("target_name"):
        return str(args["target_name"])
    if isinstance(args.get("path"), str) and args.get("path"):
        return str(args["path"])
    return fallback or step.step_id


@REGISTRY.register("mirror.read", capability="read")
def rune_mirror_read(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    source = args.get("input")
    if source is None:
        raise StepExecutionError("mirror.read requires 'input'")
    source = ctx.resolve(source)

    def read_one(item: Any) -> str:
        if isinstance(item, str) and item.startswith("file://"):
            p = Path(item[7:])
            p = p if p.is_absolute() else (ctx.spell.source_path.parent / p).resolve()
            return p.read_text(encoding="utf-8")
        if isinstance(item, str):
            path = ctx.maybe_path(item)
            return path.read_text(encoding="utf-8") if path.exists() else item
        return str(item)

    value = [read_one(i) for i in source] if isinstance(source, list) else read_one(source)
    return RuneOutcome(value, 0.98, "Source texts may still contain omissions or transcription noise.")


@REGISTRY.register("mirror.mcp_read_resources", capability="read")
def rune_mcp_read_resources(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    raw_cmd = ctx.resolve(args.get("server_cmd"))
    if raw_cmd is None:
        raise StepExecutionError("mirror.mcp_read_resources requires 'server_cmd'")
    cmd = shlex.split(raw_cmd) if isinstance(raw_cmd, str) else [str(part) for part in raw_cmd]
    if cmd and cmd[0].lower() in ("python", "python3"):
        cmd[0] = sys.executable
    if len(cmd) >= 2 and cmd[1].endswith(".py") and not Path(cmd[1]).is_absolute():
        cmd[1] = str(ctx.rel_path(cmd[1]))
    ctx.record_capability_event(
        kind="process.spawn",
        step_id=step.step_id,
        rune=step.rune,
        target={"cmd": cmd[:3], "cmd_len": len(cmd)},
    )
    client = MCPClient(cmd)
    ctx.add_mcp_client(client)
    resources = client.list_resources()
    uris = ctx.resolve(args.get("uris"))
    pattern = args.get("pattern")
    if uris:
        wanted = {str(uri) for uri in uris}
        selected = [resource for resource in resources if resource.get("uri") in wanted]
    elif pattern:
        regex = re.compile(str(pattern))
        selected = [
            resource
            for resource in resources
            if regex.search(str(resource.get("name", ""))) or regex.search(str(resource.get("title", "")))
        ]
    else:
        selected = resources
    texts: List[str] = []
    for resource in selected:
        for item in client.read_resource(str(resource["uri"])):
            if item.get("text") is not None:
                texts.append(str(item["text"]))
    return RuneOutcome({"resources": selected, "texts": texts}, 0.96, "MCP resources reflect what the server chose to expose at read time.")


@REGISTRY.register("lantern.classify", capability="reason")
def rune_classify(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    items = ctx.resolve(args.get("items", []))
    items = items if isinstance(items, list) else [items]
    labels = []
    for item in items:
        text = str(item).lower()
        label = (
            "urgent"
            if any(word in text for word in ["urgent", "asap", "immediately", "today"])
            else "finance"
            if any(word in text for word in ["invoice", "receipt", "payment"])
            else "normal"
        )
        labels.append({"text": item, "label": label})
    return RuneOutcome(labels, 0.82, "Keyword triage is heuristic and may miss context or tone.")


@REGISTRY.register("forge.template", capability="transform")
def rune_template(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    template = str(ctx.resolve(args.get("template", "")))
    bindings = ctx.resolve(args.get("bindings", {}))
    if not isinstance(bindings, dict):
        raise StepExecutionError("forge.template bindings must resolve to an object")
    try:
        rendered = template.format(**bindings)
    except KeyError as exc:
        raise StepExecutionError(f"forge.template missing binding: {exc}") from exc
    return RuneOutcome(rendered, 0.97)


@REGISTRY.register("forge.summarize", capability="transform")
def rune_summarize(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    content = ctx.resolve(args.get("from", ""))
    title = str(ctx.resolve(args.get("title", "Synthesized Brief")))
    if isinstance(content, dict) and "texts" in content:
        texts = [str(item) for item in content.get("texts", [])]
    elif isinstance(content, list):
        texts = [str(item) for item in content]
    else:
        texts = [str(content)]
    lexicon = {
        "rules and limits": ["rule", "rules", "limit", "limits", "cost", "costs", "constraint", "constraints"],
        "magical culture": ["culture", "belief", "beliefs", "superstition", "ritual", "rituals", "social", "legitimacy"],
        "science and programmability": ["science", "scientific", "code", "coding", "programmable", "programming", "rune", "runes"],
        "problem solving over power scaling": ["problem", "problems", "power", "scaling", "tests", "creativity", "critical"],
        "meta systems and protocols": ["meta", "protocol", "protocols", "audience", "narrative", "interface", "genre"],
        "mystery and belief": ["mystery", "unknown", "belief", "believe", "wonder", "faith", "magical"],
        "pluralistic and synergistic structures": ["pluralistic", "synergistic", "definitive", "wild", "thematic"],
    }
    lowered = [text.lower() for text in texts]
    scored = []
    for theme, needles in lexicon.items():
        score = sum(1 for text in lowered if any(needle in text for needle in needles))
        if score:
            scored.append((theme, score))
    themes = [theme for theme, _score in sorted(scored, key=lambda item: (-item[1], item[0]))[:5]]
    lines: List[str] = [f"# {title}", "", "### Recurring themes"]
    for theme in themes or ["(no stable themes extracted)"]:
        lines.append(f"- {theme}")
    lines.append("")
    for idx, text in enumerate(texts, start=1):
        non_empty = [line.strip() for line in text.splitlines() if line.strip()]
        lines.append(f"## Source {idx}")
        previews = non_empty[:4] if non_empty else [text[:180].strip() or "(empty)"]
        for preview in previews:
            lines.append(f"- {preview[:300]}")
    lines += [
        "",
        "### Axiomurgy notes",
        "- Strong systems benefit from explicit rules, social interpretation, and programmable forms.",
        "- Conflicts become more interesting when the system rewards counters, verification, and creativity over raw power.",
        "- A believable magical culture needs both the rules that work and the rituals people merely believe work.",
    ]
    return RuneOutcome(
        "\n".join(lines),
        0.85,
        "This is a controlled extractive synthesis based on a theme lexicon and leading lines, not a semantic model.",
    )


@REGISTRY.register("forge.reply_drafts", capability="transform")
def rune_reply_drafts(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    items = ctx.resolve(args.get("items", []))
    items = items if isinstance(items, list) else [items]
    drafts = []
    for item in items:
        text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
        label = str(item.get("label", "normal")) if isinstance(item, dict) else "normal"
        if label == "urgent":
            reply = "Acknowledged. I have flagged this as urgent and prepared it for immediate human review."
        elif label == "finance":
            reply = "Thanks. I have routed this to the finance queue and prepared a draft response for confirmation."
        else:
            reply = "Thanks for the message. I have prepared a brief reply draft for review."
        drafts.append({"original": text, "label": label, "draft": reply})
    return RuneOutcome(drafts, 0.9, "Drafts are templated and may need tone or factual review.")


@REGISTRY.register("seal.review", capability="verify")
def rune_review(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    artifact = ctx.resolve(args.get("from"))
    markers = list(ctx.resolve(args.get("must_include", [])))
    text = artifact if isinstance(artifact, str) else json.dumps(artifact, ensure_ascii=False)
    missing = [marker for marker in markers if marker not in text]
    return RuneOutcome(
        {
            "approved": not missing,
            "missing": missing,
            "artifact": artifact,
            "note": "Minimal review only. Replace with stronger policy and citation checks in production.",
        },
        0.97,
        "Review is marker-based and does not prove semantic correctness.",
    )


@REGISTRY.register("seal.require", capability="verify")
def rune_require(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    value = ctx.resolve(args.get("value"))
    equals = ctx.resolve(args.get("equals", True))
    message = str(ctx.resolve(args.get("message", "seal.require failed")))
    if value != equals:
        raise StepExecutionError(message)
    return RuneOutcome({"ok": True, "value": value, "equals": equals}, 1.0)


@REGISTRY.register("seal.assert_jsonschema", capability="verify")
def rune_assert_jsonschema(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    target = ctx.resolve(args.get("target"))
    if "schema" not in args:
        raise StepExecutionError("seal.assert_jsonschema requires 'schema'")
    schema = load_schema(args.get("schema"), ctx.spell.source_path.parent)
    label = target_label(step, args, step.step_id)
    try:
        jsonschema.validate(instance=target, schema=schema)
    except jsonschema.ValidationError as exc:
        proof = build_proof(
            step.rune,
            label,
            "failed",
            f"JSON Schema validation failed for {label}: {exc.message}",
            {"validator": "jsonschema", "path": list(exc.path), "schema_path": list(exc.schema_path)},
        )
        raise ProofFailure(proof["message"], proof) from exc
    proof = build_proof(step.rune, label, "passed", f"JSON Schema validation passed for {label}.", {"validator": "jsonschema"})
    return RuneOutcome({"ok": True, "proof": proof}, 1.0)


@REGISTRY.register("seal.assert_markers", capability="verify")
def rune_assert_markers(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    target = ctx.resolve(args.get("target"))
    markers = [str(item) for item in ctx.resolve(args.get("markers", []))]
    if not markers:
        raise StepExecutionError("seal.assert_markers requires a non-empty 'markers' list")
    case_sensitive = bool(args.get("case_sensitive", True))
    text = coerce_text(ctx, target)
    haystack = text if case_sensitive else text.lower()
    missing: List[str] = []
    matched: List[str] = []
    for marker in markers:
        needle = marker if case_sensitive else marker.lower()
        if needle in haystack:
            matched.append(marker)
        else:
            missing.append(marker)
    label = target_label(step, args, step.step_id)
    if missing:
        proof = build_proof(
            step.rune,
            label,
            "failed",
            f"Marker assertion failed for {label}. Missing markers: {missing}",
            {"matched": matched, "missing": missing},
        )
        raise ProofFailure(proof["message"], proof)
    proof = build_proof(step.rune, label, "passed", f"Marker assertion passed for {label}.", {"matched": matched, "missing": []})
    return RuneOutcome({"ok": True, "proof": proof}, 1.0)


@REGISTRY.register("seal.assert_contains_sections", capability="verify")
def rune_assert_contains_sections(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    target = ctx.resolve(args.get("target"))
    sections = [str(item) for item in ctx.resolve(args.get("sections", []))]
    if not sections:
        raise StepExecutionError("seal.assert_contains_sections requires a non-empty 'sections' list")
    text = coerce_text(ctx, target)
    headings = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            headings.append(line.lstrip("#").strip())
    missing = [section for section in sections if section not in headings]
    label = target_label(step, args, step.step_id)
    if missing:
        proof = build_proof(
            step.rune,
            label,
            "failed",
            f"Section assertion failed for {label}. Missing sections: {missing}",
            {"headings": headings, "missing": missing},
        )
        raise ProofFailure(proof["message"], proof)
    proof = build_proof(
        step.rune,
        label,
        "passed",
        f"Section assertion passed for {label}.",
        {"headings": headings, "missing": []},
    )
    return RuneOutcome({"ok": True, "proof": proof}, 1.0)


@REGISTRY.register("seal.assert_path_exists", capability="verify")
def rune_assert_path_exists(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    raw_path = ctx.resolve(args.get("path", args.get("target")))
    if raw_path is None:
        raise StepExecutionError("seal.assert_path_exists requires 'path' or 'target'")
    path = ctx.maybe_path(str(raw_path))
    label = target_label(step, args, str(path))
    if not path.exists():
        proof = build_proof(step.rune, label, "failed", f"Expected path to exist: {path}", {"path": str(path), "exists": False})
        raise ProofFailure(proof["message"], proof)
    proof = build_proof(step.rune, label, "passed", f"Verified path exists: {path}", {"path": str(path), "exists": True})
    return RuneOutcome({"ok": True, "proof": proof, "path": str(path)}, 1.0)


@REGISTRY.register("seal.approval_gate", capability="approve")
def rune_approval_gate(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    reason = str(ctx.resolve(args.get("reason", "Human approval required.")))
    approved = bool(args.get("auto_approve", False)) or step.step_id in ctx.approvals or "all" in ctx.approvals
    if not approved:
        raise ApprovalRequiredError(reason)
    return RuneOutcome({"approved": True, "reason": reason}, 1.0)


@REGISTRY.register("gate.archive", capability="write")
def rune_archive(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    artifact = ctx.resolve(args.get("from"))
    count = len(artifact) if isinstance(artifact, list) else 1
    ctx.record_capability_event(
        kind="filesystem.write",
        step_id=step.step_id,
        rune=step.rune,
        target={"count": count, "mode": "simulate" if ctx.simulate else "archive"},
    )
    return RuneOutcome({"archived": count, "status": "simulated_archive" if ctx.simulate else "archive_complete"}, 0.98, side_effect=not ctx.simulate)


@REGISTRY.register("gate.emit", capability="write")
def rune_emit(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    artifact = ctx.resolve(args.get("from"))
    target = str(ctx.resolve(args.get("target", "stdout")))
    ctx.record_capability_event(
        kind="filesystem.write",
        step_id=step.step_id,
        rune=step.rune,
        target={"target": target},
    )
    status = "simulated_write" if ctx.simulate else "emitted"
    return RuneOutcome({"target": target, "emitted": artifact, "status": status}, 0.98, side_effect=not ctx.simulate)


@REGISTRY.register("gate.file_write", capability="write")
def rune_file_write(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    artifact = ctx.resolve(args.get("from"))
    raw_path = ctx.resolve(args.get("path"))
    if raw_path is None:
        raise StepExecutionError("gate.file_write requires 'path'")
    target = ctx.maybe_path(str(raw_path))
    ctx.record_capability_event(
        kind="filesystem.write",
        step_id=step.step_id,
        rune=step.rune,
        target=str(target),
    )
    if ctx.simulate:
        payload = artifact if isinstance(artifact, str) else json.dumps(artifact, ensure_ascii=False)
        return RuneOutcome(
            {
                "path": str(target),
                "mode": "text",
                "status": "simulated_write",
                "bytes": len(payload.encode("utf-8")),
            },
            0.99,
        )
    result = ctx.write_json(target, artifact) if isinstance(artifact, (dict, list)) else ctx.write_text(target, str(artifact))
    return RuneOutcome(result, 0.99, side_effect=True)


@REGISTRY.register("gate.openapi_call", capability="write")
def rune_openapi_call(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    spec_path = ctx.rel_path(str(ctx.resolve(args.get("spec"))))
    operation_id = str(ctx.resolve(args.get("operationId")))
    arguments = ctx.resolve(args.get("arguments", {}))
    spec = load_yaml(spec_path)
    server_url = str(spec.get("servers", [{"url": ""}])[0]["url"]).rstrip("/")
    method = None
    raw_path = None
    operation = None
    path_params: List[Dict[str, Any]] = []
    for path, item in spec.get("paths", {}).items():
        path_params = list(item.get("parameters", []))
        for candidate_method in ["get", "post", "put", "patch", "delete"]:
            candidate = item.get(candidate_method)
            if candidate and candidate.get("operationId") == operation_id:
                method = candidate_method.upper()
                raw_path = path
                operation = candidate
                break
        if operation is not None:
            break
    if operation is None or method is None or raw_path is None:
        raise StepExecutionError(f"operationId not found in spec: {operation_id}")
    path_values = dict(arguments.get("path", {}))
    query_values = dict(arguments.get("query", {}))
    body = arguments.get("body")
    url_path = raw_path
    for parameter in path_params + list(operation.get("parameters", [])):
        if parameter.get("in") == "path":
            name = parameter["name"]
            if name not in path_values:
                raise StepExecutionError(f"Missing path parameter '{name}' for {operation_id}")
            url_path = url_path.replace("{" + name + "}", str(path_values[name]))
    url = server_url + url_path
    if ctx.simulate:
        return RuneOutcome(
            {"status": "simulated_http_call", "method": method, "url": url, "body": body, "query": query_values},
            0.96,
            "Simulation skips the remote server and assumes the contract is still accurate.",
        )
    ctx.record_capability_event(
        kind="network.http",
        step_id=step.step_id,
        rune=step.rune,
        target={"method": method, "url": url},
    )
    response = requests.request(method, url, json=body, params=query_values, timeout=10)
    response_body = response.json() if "application/json" in response.headers.get("Content-Type", "") else response.text
    response_key = str(response.status_code)
    media = operation.get("responses", {}).get(response_key, {}).get("content", {}).get("application/json", {})
    if media.get("schema") and isinstance(response_body, dict):
        jsonschema.validate(instance=response_body, schema=media["schema"])
    return RuneOutcome(
        {
            "status": "http_call_complete",
            "method": method,
            "url": url,
            "status_code": response.status_code,
            "body": response_body,
        },
        0.99,
        "Remote systems can still drift after the call completes.",
        method in HTTP_WRITE_METHODS,
    )


@REGISTRY.register("gate.mcp_call_tool", capability="write")
def rune_mcp_call_tool(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    raw_cmd = ctx.resolve(args.get("server_cmd"))
    name = str(ctx.resolve(args.get("name")))
    arguments = ctx.resolve(args.get("arguments", {}))
    if raw_cmd is None:
        raise StepExecutionError("gate.mcp_call_tool requires 'server_cmd'")
    cmd = shlex.split(raw_cmd) if isinstance(raw_cmd, str) else [str(part) for part in raw_cmd]
    if cmd and cmd[0].lower() in ("python", "python3"):
        cmd[0] = sys.executable
    if len(cmd) >= 2 and cmd[1].endswith(".py") and not Path(cmd[1]).is_absolute():
        cmd[1] = str(ctx.rel_path(cmd[1]))
    if ctx.simulate:
        return RuneOutcome(
            {"status": "simulated_mcp_tool_call", "tool": name, "arguments": arguments},
            0.95,
            "Simulation skips the remote MCP server and any external effects.",
        )
    ctx.record_capability_event(
        kind="process.spawn",
        step_id=step.step_id,
        rune=step.rune,
        target={"cmd": cmd[:3], "cmd_len": len(cmd)},
    )
    client = MCPClient(cmd)
    ctx.add_mcp_client(client)
    return RuneOutcome(client.call_tool(name, arguments), 0.97, "Tool behavior depends on the remote MCP server implementation.", True)



def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run, inspect, plan, or lint an Axiomurgy spell or spellbook entrypoint.")
    parser.add_argument("target", help="Path to a .spell.json file, a spellbook directory, or spellbook.json")
    parser.add_argument("--entrypoint", default=None, help="Spellbook entrypoint name")
    parser.add_argument("--approve", action="append", default=[])
    parser.add_argument("--policy", default=None)
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--capability", action="append", default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--describe", action="store_true", help="Describe the resolved spell or spellbook entrypoint without executing it")
    mode.add_argument("--plan", action="store_true", help="Compile a dry execution plan and approval manifest without executing side effects")
    mode.add_argument("--lint", action="store_true", help="Lint a spell or spellbook deterministically without executing it")
    mode.add_argument("--review-bundle", action="store_true", help="Emit a single JSON review bundle (describe + lint + plan + fingerprints)")
    mode.add_argument("--verify-review-bundle", default=None, help="Verify current state against a reviewed bundle JSON")
    parser.add_argument("--manifest-out", default=None, help="Optional path to write the approval manifest JSON when using --plan")
    parser.add_argument("--review-bundle-in", default=None, help="Optional reviewed bundle JSON to attest execution against")
    parser.add_argument("--cycle-config", default=None, help="Optional Ouroboros Chamber cyclic runner config JSON (opt-in)")
    parser.add_argument(
        "--enforce-review-bundle",
        action="store_true",
        help="Enforce the reviewed capability envelope as a vessel (requires --review-bundle-in)",
    )
    parser.add_argument(
        "--replay-revolution-dir",
        default=None,
        help="v2.0: directory for a single revolution (e.g. .../revolutions/rev_0001) to replay from replay_record.json",
    )
    parser.add_argument(
        "--replay-run-manifest",
        default=None,
        help="v2.0: run_manifest.json path; use with --replay-revolution-id",
    )
    parser.add_argument(
        "--replay-revolution-id",
        default=None,
        help="v2.0: revolution id (e.g. rev_0001) when using --replay-run-manifest",
    )
    parser.add_argument(
        "--replay-artifact-dir",
        default=None,
        help="v2.0: output directory for replay witnesses (default: temp dir derived from revolution path)",
    )
    parser.add_argument(
        "--export-vermyth-program",
        default=None,
        metavar="PATH",
        help="Write Vermyth SemanticProgram JSON to PATH and exit (standalone; no --describe/--plan/--lint/--review-bundle)",
    )
    parser.add_argument(
        "--vermyth-program",
        action="store_true",
        help="Include vermyth_program_export in --plan / --review-bundle output",
    )
    parser.add_argument(
        "--vermyth-validate",
        action="store_true",
        help="Attach vermyth_program_preview via Vermyth compile_program (needs AXIOMURGY_VERMYTH_BASE_URL)",
    )
    parser.add_argument(
        "--vermyth-recommendations",
        action="store_true",
        help="Include semantic_recommendations in plan output (needs AXIOMURGY_VERMYTH_BASE_URL)",
    )
    parser.add_argument(
        "--vermyth-receipt",
        action="store_true",
        help="When recording witnesses, also write *.vermyth_receipt.json (unsigned mapping)",
    )
    return parser.parse_args(argv)


def _load_run_manifest_for_replay(manifest_path: Path) -> Dict[str, Any]:
    """Prefer raw manifest so artifact_root is a real path; diff manifests may redact paths."""
    p = Path(manifest_path).resolve()
    doc = load_json(p)
    rc = doc.get("run_capsule") or {}
    art = rc.get("artifact_root")
    if isinstance(art, str) and art and not art.startswith("<"):
        try:
            if Path(art).is_dir():
                return doc
        except OSError:
            pass
    raw_sibling = p.with_name(p.name.replace(".run_manifest.json", ".run_manifest.raw.json"))
    if raw_sibling.is_file() and raw_sibling != p:
        return load_json(raw_sibling)
    raise SpellValidationError(
        "cannot resolve run capsule root from manifest (use *.run_manifest.raw.json or a path with real artifact_root)"
    )


def _revolution_dir_from_run_manifest(manifest_path: Path, revolution_id: str) -> Path:
    doc = _load_run_manifest_for_replay(Path(manifest_path))
    rc = doc.get("run_capsule") or {}
    art = rc.get("artifact_root")
    if not isinstance(art, str) or not art:
        raise SpellValidationError("run manifest missing run_capsule.artifact_root")
    return Path(art).resolve() / "revolutions" / revolution_id



def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"ERROR: File not found: {target}")
        return 2
    if args.manifest_out and not (args.plan or args.review_bundle):
        print("ERROR: --manifest-out can only be used with --plan or --review-bundle")
        return 2
    if args.export_vermyth_program and (
        args.describe or args.plan or args.lint or args.review_bundle or args.verify_review_bundle or args.cycle_config
    ):
        print(
            "ERROR: --export-vermyth-program is standalone only "
            "(do not combine with --describe/--plan/--lint/--review-bundle/--verify-review-bundle/--cycle-config)"
        )
        return 2
    if args.enforce_review_bundle and not args.review_bundle_in:
        print("ERROR: --enforce-review-bundle requires --review-bundle-in")
        return 2
    if args.cycle_config and (args.describe or args.plan or args.lint or args.review_bundle or args.verify_review_bundle):
        print("ERROR: --cycle-config is only valid for execution mode")
        return 2
    if args.simulate and (args.describe or args.plan or args.lint):
        print("ERROR: --simulate is only valid for execution mode")
        return 2
    try:
        policy_override = Path(args.policy).resolve() if args.policy else None
        artifact_override = Path(args.artifact_dir).resolve() if args.artifact_dir else None
        if args.export_vermyth_program:
            resolved = resolve_run_target(target, args.entrypoint, policy_override, artifact_override)
            from .vermyth_export import build_vermyth_program_export

            out_path = Path(args.export_vermyth_program).resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json_dumps(build_vermyth_program_export(resolved.spell)), encoding="utf-8")
            print(json_dumps({"mode": "export_vermyth_program", "path": str(out_path)}))
            return 0
        if args.lint:
            result = lint_target(target, policy_override=policy_override)
        else:
            resolved = resolve_run_target(target, args.entrypoint, policy_override, artifact_override)
            want_replay = bool(
                args.replay_revolution_dir or args.replay_run_manifest or args.replay_revolution_id
            )
            if want_replay:
                if args.describe or args.plan or args.lint or args.review_bundle or args.verify_review_bundle or args.cycle_config:
                    print(
                        "ERROR: replay is only valid without --describe/--plan/--lint/"
                        "--review-bundle/--verify-review-bundle/--cycle-config"
                    )
                    return 2
                if args.replay_run_manifest and not args.replay_revolution_id:
                    print("ERROR: --replay-run-manifest requires --replay-revolution-id")
                    return 2
                if args.replay_revolution_dir and args.replay_run_manifest:
                    print("ERROR: use only one of --replay-revolution-dir or --replay-run-manifest")
                    return 2
                if not args.replay_revolution_dir and not args.replay_run_manifest:
                    print("ERROR: specify --replay-revolution-dir or --replay-run-manifest")
                    return 2
                if args.replay_run_manifest and args.replay_revolution_id:
                    revolution_dir = _revolution_dir_from_run_manifest(
                        Path(args.replay_run_manifest), str(args.replay_revolution_id)
                    )
                else:
                    revolution_dir = Path(args.replay_revolution_dir).resolve()
                if not revolution_dir.is_dir():
                    print(f"ERROR: revolution directory not found: {revolution_dir}")
                    return 2
                reviewed_in = (
                    load_json(Path(args.review_bundle_in).resolve()) if args.review_bundle_in else None
                )
                if args.replay_artifact_dir:
                    rar = Path(args.replay_artifact_dir).resolve()
                else:
                    digest = sha256_bytes(str(revolution_dir.resolve()).encode("utf-8"))[:16]
                    rar = Path(tempfile.gettempdir()) / f"axiomurgy_replay_{digest}"
                rar.mkdir(parents=True, exist_ok=True)
                result = replay_ouroboros_revolution(
                    resolved,
                    revolution_dir=revolution_dir,
                    approvals=set(args.approve),
                    simulate=bool(args.simulate),
                    reviewed_bundle=reviewed_in,
                    enforce_review_bundle=bool(args.enforce_review_bundle),
                    replay_artifact_root=rar,
                )
                print(json_dumps(result))
                if result["replay_status"] == "non_replayable":
                    return 4
                if result["replay_status"] == "drift":
                    return 3
                return 0
            if args.describe:
                result = describe_target(resolved)
            elif args.verify_review_bundle:
                reviewed = load_json(Path(args.verify_review_bundle).resolve())
                current_bundle = build_review_bundle(resolved, approvals=set(args.approve))
                cmp = compare_reviewed_bundle(reviewed, current_bundle)
                result = {"mode": "verify", **cmp}
                print(json_dumps(result))
                return 0 if result["status"] in ("exact", "partial") else 3
            elif args.review_bundle:
                result = build_review_bundle(
                    resolved,
                    approvals=set(args.approve),
                    vermyth_program=bool(args.vermyth_program),
                    vermyth_validate=bool(args.vermyth_validate),
                    vermyth_recommendations=bool(args.vermyth_recommendations),
                )
                if args.manifest_out:
                    manifest_path = Path(args.manifest_out).resolve()
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(json_dumps(result["approval_manifest"]), encoding="utf-8")
                    result["manifest_path"] = str(manifest_path)
            elif args.plan:
                result = build_plan_summary(
                    resolved,
                    approvals=set(args.approve),
                    simulate=False,
                    vermyth_program=bool(args.vermyth_program),
                    vermyth_validate=bool(args.vermyth_validate),
                    vermyth_recommendations=bool(args.vermyth_recommendations),
                )
                if args.manifest_out:
                    manifest_path = Path(args.manifest_out).resolve()
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(json_dumps(result["manifest"]), encoding="utf-8")
                    result["manifest_path"] = str(manifest_path)
            else:
                capabilities = {"read", "memory", "reason", "transform", "verify", "approve", "simulate", "write"}
                capabilities.update(args.capability)
                reviewed_in = load_json(Path(args.review_bundle_in).resolve()) if args.review_bundle_in else None
                from . import vermyth_integration as _vermyth

                policy_doc = load_json(resolved.policy_path)
                gate_record = _vermyth.run_vermyth_gate(resolved.spell, policy_doc)
                gate_for_result = None if gate_record.get("status") == "skipped" else gate_record
                v_notes = _vermyth.vermyth_gate_policy_notes(gate_record)
                rec_emit = bool(args.vermyth_receipt) or _vermyth.should_emit_receipt()
                rb_path = str(Path(args.review_bundle_in).resolve()) if args.review_bundle_in else None
                if args.cycle_config:
                    result = ouroboros_chamber(
                        resolved,
                        cycle_config_path=Path(args.cycle_config).resolve(),
                        approvals=set(args.approve),
                        simulate=bool(args.simulate),
                        reviewed_bundle=reviewed_in,
                        enforce_review_bundle=bool(args.enforce_review_bundle),
                    )
                else:
                    result = execute_spell(
                        resolved.spell,
                        sorted(capabilities),
                        set(args.approve),
                        bool(args.simulate),
                        resolved.policy_path,
                        resolved.artifact_dir,
                        reviewed_bundle=reviewed_in,
                        enforce_review_bundle=bool(args.enforce_review_bundle),
                        vermyth_policy_notes=v_notes,
                        vermyth_gate_record=gate_for_result,
                        reviewed_bundle_path=rb_path,
                        vermyth_receipt_emit=rec_emit,
                    )
                if args.review_bundle_in:
                    reviewed = load_json(Path(args.review_bundle_in).resolve())
                    att = compute_attestation(reviewed, resolved, approvals=set(args.approve))
                    overreach = ((result.get("capabilities") or {}).get("overreach")) or []
                    if overreach:
                        att["status"] = "mismatch"
                        att["diffs"].append(
                            {
                                "path": "capabilities.overreach",
                                "reviewed": (((reviewed.get("capabilities") or {}).get("envelope") or {}).get("kinds")),
                                "current": sorted((result.get("capabilities") or {}).get("used") or []),
                                "severity": "required",
                                "note": f"Undeclared capability use detected: {sorted(overreach)}",
                            }
                        )
                    result["attestation"] = {**att, "reviewed_bundle_path": str(Path(args.review_bundle_in).resolve())}
                    # v1.0 execution outcomes: enforcement vs observed attestation.
                    if result.get("status") != "succeeded" and (result.get("blocked") or {}).get("source") == "review_envelope":
                        result["execution_outcome"] = "blocked_overreach"
                    elif att["status"] == "exact":
                        result["execution_outcome"] = "executed_exact"
                    else:
                        result["execution_outcome"] = "executed_partial"
                else:
                    result["attestation"] = {"status": "none", "diffs": [], "reviewed_bundle_path": None}
                    result["execution_outcome"] = "executed_partial" if result.get("status") == "succeeded" else "blocked_policy"
                if resolved.spellbook is not None:
                    result["spellbook"] = {
                        "name": resolved.spellbook.name,
                        "version": resolved.spellbook.version,
                        "entrypoint": resolved.entrypoint,
                        "path": str(resolved.spellbook.source_path),
                    }
    except (AxiomurgyError, json.JSONDecodeError, FileNotFoundError, requests.RequestException, jsonschema.ValidationError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json_dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
