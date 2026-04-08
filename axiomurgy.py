#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import heapq
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import jsonschema
import requests
import yaml

VERSION = "1.6.0"
ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = ROOT / "policies" / "default.policy.json"
DEFAULT_SCHEMA_PATH = ROOT / "spell.schema.json"
DEFAULT_SPELLBOOK_SCHEMA_PATH = ROOT / "spellbook.schema.json"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"
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


def extract_declared_input_paths(spell: "Spell") -> List[Path]:
    paths: List[Path] = []
    for step in list(spell.graph) + list(spell.rollback):
        if step.rune == "mirror.read":
            raw = step.args.get("input")
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and not item.startswith("$"):
                        paths.append(Path(item[7:]) if item.startswith("file://") else Path(item))
            elif isinstance(raw, str) and not raw.startswith("$"):
                paths.append(Path(raw[7:]) if raw.startswith("file://") else Path(raw))
        if step.rune == "seal.assert_path_exists":
            raw = step.args.get("path")
            if isinstance(raw, str) and not raw.startswith("$"):
                paths.append(Path(raw))
    seen: Set[str] = set()
    out: List[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def classify_input_manifest(spell: "Spell") -> Dict[str, Any]:
    declared_static: List[Dict[str, Any]] = []
    declared_dynamic: List[Dict[str, Any]] = []
    unresolved_dynamic: List[Dict[str, Any]] = []

    def add_static(spec: str, step_id: str, rune: str) -> None:
        declared_static.append({"spec": spec, "step_id": step_id, "rune": rune})

    def add_declared_dynamic(spec: Any, step_id: str, rune: str, note: str) -> None:
        declared_dynamic.append({"spec": spec, "step_id": step_id, "rune": rune, "note": note})

    def add_unresolved(spec: Any, step_id: str, rune: str, note: str) -> None:
        unresolved_dynamic.append({"spec": spec, "step_id": step_id, "rune": rune, "note": note})

    for step in list(spell.graph) + list(spell.rollback):
        if step.rune == "mirror.read":
            raw = step.args.get("input")
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str) and not item.startswith("$"):
                        add_static(item, step.step_id, step.rune)
                    elif isinstance(item, str) and item.startswith("$"):
                        add_declared_dynamic(item, step.step_id, step.rune, "mirror.read input references runtime value")
            elif isinstance(raw, str):
                if raw.startswith("$inputs"):
                    # declared dynamic, but often resolvable from spell.inputs
                    add_declared_dynamic(raw, step.step_id, step.rune, "mirror.read input references spell inputs")
                elif raw.startswith("$"):
                    add_unresolved(raw, step.step_id, step.rune, "mirror.read input references non-input runtime value")
                else:
                    add_static(raw, step.step_id, step.rune)
        if step.rune == "seal.assert_path_exists":
            raw = step.args.get("path")
            if isinstance(raw, str) and raw.startswith("$"):
                add_unresolved(raw, step.step_id, step.rune, "path is computed dynamically at runtime")
            elif isinstance(raw, str):
                add_static(raw, step.step_id, step.rune)

    return {
        "declared_static": declared_static,
        "declared_dynamic": declared_dynamic,
        "unresolved_dynamic": unresolved_dynamic,
        "summary": {
            "declared_static": len(declared_static),
            "declared_dynamic": len(declared_dynamic),
            "unresolved_dynamic": len(unresolved_dynamic),
            "unresolved_dynamic_present": bool(unresolved_dynamic),
        },
    }


def extract_output_schema_paths(spell: "Spell") -> List[Path]:
    out: List[Path] = []
    for step in list(spell.graph) + list(spell.rollback):
        if isinstance(step.output_schema, str) and step.output_schema:
            out.append(Path(step.output_schema))
    return out


def compute_spell_fingerprints(spell: "Spell", policy_path: Path, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    repo_root = repo_root or ROOT
    files: List[Dict[str, Any]] = []
    files.append(file_digest_entry(spell.source_path, repo_root=repo_root, role="spell"))
    files.append(file_digest_entry(policy_path, repo_root=repo_root, role="policy"))
    files.append(file_digest_entry(DEFAULT_SCHEMA_PATH, repo_root=repo_root, role="schema:spell"))
    files.append(file_digest_entry(DEFAULT_SPELLBOOK_SCHEMA_PATH, repo_root=repo_root, role="schema:spellbook"))
    for schema_path in extract_output_schema_paths(spell):
        resolved = (spell.source_path.parent / schema_path).resolve() if not schema_path.is_absolute() else schema_path.resolve()
        files.append(file_digest_entry(resolved, repo_root=repo_root, role="schema:output"))

    input_manifest = classify_input_manifest(spell)
    input_files: List[Dict[str, Any]] = []
    for item in input_manifest["declared_static"]:
        spec = str(item["spec"])
        path = Path(spec[7:]) if spec.startswith("file://") else Path(spec)
        resolved = (spell.source_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
        input_files.append(file_digest_entry(resolved, repo_root=repo_root, role="input"))

    required = {
        "spell_sha256": sha256_file(spell.source_path),
        "policy_sha256": sha256_file(policy_path),
        "spell_schema_sha256": sha256_file(DEFAULT_SCHEMA_PATH),
        "spellbook_schema_sha256": sha256_file(DEFAULT_SPELLBOOK_SCHEMA_PATH),
    }
    return {
        "required": required,
        "files": files,
        "input_manifest": {
            "files": input_files,
            "classification": input_manifest,
            "sha256": sha256_bytes(canonical_json({"files": input_files, "classification": input_manifest}).encode("utf-8")),
        },
    }


def compute_spellbook_fingerprints(resolved: "ResolvedRunTarget", repo_root: Optional[Path] = None) -> Dict[str, Any]:
    repo_root = repo_root or ROOT
    if resolved.spellbook is None:
        return {}
    sb = resolved.spellbook
    files: List[Dict[str, Any]] = []
    files.append(file_digest_entry(sb.source_path, repo_root=repo_root, role="spellbook:manifest"))
    files.append(file_digest_entry(resolved.spell.source_path, repo_root=repo_root, role="spellbook:entrypoint_spell"))
    spellbook_dir = sb.source_path.parent
    schemas_dir = (spellbook_dir / "schemas")
    if schemas_dir.exists():
        for schema_file in sorted(schemas_dir.glob("*.json")):
            files.append(file_digest_entry(schema_file, repo_root=repo_root, role="spellbook:schema"))
    required = {
        "spellbook_manifest_sha256": sha256_file(sb.source_path),
        "spellbook_entrypoint_spell_sha256": sha256_file(resolved.spell.source_path),
    }
    return {"required": required, "files": files}


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


class RuneRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, RuneHandler] = {}
        self._capability_map: Dict[str, str] = {}

    def register(self, name: str, capability: str):
        def decorator(func: RuneHandler):
            self._handlers[name] = func
            self._capability_map[name] = capability
            return func

        return decorator

    def handler_for(self, name: str) -> RuneHandler:
        if name not in self._handlers:
            raise KeyError(f"Unknown rune: {name}")
        return self._handlers[name]

    def required_capability(self, name: str) -> str:
        if name not in self._capability_map:
            raise KeyError(f"Unknown rune: {name}")
        return self._capability_map[name]


REGISTRY = RuneRegistry()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)

def canonical_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


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
    Deterministic seal-stage acceptance (v1.6).
    Order: execution failure → reject_if (vs last accepted) → primary strict improvement
    (with guardrails) → equal-primary path with guardrails then tie_breakers.
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
    allowlist = cfg["mutation_target_allowlist"]
    max_rev = cfg["max_revolutions"]
    flux_budget = cfg["flux_budget"]
    plateau_window = cfg["plateau_window"]
    stop = cfg["stop_conditions"]
    metric_path = cfg["target_metric"]["path"]
    metric_abs = str((resolved.artifact_dir / metric_path).resolve())
    recall_cfg = cfg["recall"]
    k_succ = recall_cfg["recent_k_successes"]
    k_fail = recall_cfg["recent_k_failures"]
    reject_on_noop = cfg["reject_on_noop"]
    acc_contract = cfg["acceptance_contract"]

    proposals = expand_cycle_proposals(cfg)
    if not proposals:
        raise SpellValidationError("cycle config produced no proposals")

    chamber_dir = resolved.artifact_dir / "ouroboros"
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
        resolved.artifact_dir,
        reviewed_bundle=reviewed_bundle,
        enforce_review_bundle=enforce_review_bundle,
    )
    baseline_score = float("-inf")
    if baseline_result.get("status") == "succeeded":
        try:
            baseline_score = _fixture_score(resolved.artifact_dir, metric_path)
        except Exception:
            baseline_score = float("-inf")
    best_score = baseline_score
    best_ordering_index = None

    metric_paths_set: Set[str] = {metric_path}
    for g in acc_contract.get("guardrails") or []:
        metric_paths_set.add(str(g["metric_path"]))
    initial_metrics: Dict[str, float] = {
        p: _fixture_score_safe(resolved.artifact_dir, p) for p in sorted(metric_paths_set)
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
        resolved.artifact_dir,
        resolved.spell.name,
        plan_doc,
    )
    ranked_list: List[Dict[str, Any]] = list(plan_doc["ranked_proposals"])
    preflight_skips: List[Dict[str, Any]] = []

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
                }
            )
            continue

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

        score_before = best_score
        attempted += 1
        exec_result = execute_spell(
            load_spell(shadow_path),
            ["approve", "read", "reason", "simulate", "transform", "verify", "write"],
            approvals,
            simulate,
            resolved.policy_path,
            resolved.artifact_dir,
            reviewed_bundle=reviewed_bundle,
            enforce_review_bundle=enforce_review_bundle,
        )
        if exec_result.get("status") == "succeeded":
            try:
                score = _fixture_score(resolved.artifact_dir, metric_path)
            except Exception:
                score = float("-inf")
        else:
            score = float("-inf")

        exec_ok = exec_result.get("status") == "succeeded"
        seal = evaluate_acceptance_contract(
            artifact_dir=resolved.artifact_dir,
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
        _record_seal_acceptance_summary(acceptance_summary, seal)
        accepted = seal.get("decision") == "accept"
        accept_reject_reason = (seal.get("reasons") or ["contract_unknown"])[0]
        if accepted and cfg["require_approval_for_accept"] and "accept" not in approvals:
            accepted = False
            accept_reject_reason = "approval_required"

        if accepted:
            best_score = score
            best_spell = load_spell(shadow_path)
            best_ordering_index = ord_idx
            no_improve = 0
            accepted_mutation_count += 1
            snap_m = {p: _fixture_score_safe(resolved.artifact_dir, p) for p in metric_paths_set}
            metrics_at_best = snap_m
            metrics_at_last_accept = dict(snap_m)
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
        "nondeterministic_fields": [],
    }
    raw_path = resolved.artifact_dir / f"{resolved.spell.name}.ouroboros.raw.json"
    diff_path = resolved.artifact_dir / f"{resolved.spell.name}.ouroboros.json"
    raw_path.write_text(canonical_json(witness), encoding="utf-8")
    diff_path.write_text(canonical_json(normalize_paths_for_portability(json.loads(canonical_json(witness)), repo_root=ROOT)), encoding="utf-8")
    return {
        "mode": "cycle",
        "status": "completed",
        "stop_reason": stop_reason,
        "baseline_score": baseline_score,
        "best_score": best_score,
        "ouroboros_witness_path": str(diff_path),
        "ouroboros_witness_raw_path": str(raw_path),
        "proposal_plan_path": str(proposal_plan_diff_path),
        "proposal_plan_raw_path": str(proposal_plan_raw_path),
        "flux_attempts": attempted,
        "acceptance_contract": acc_contract,
        "acceptance_summary": acceptance_summary,
    }

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())

def file_digest_entry(path: Path, repo_root: Optional[Path] = None, role: Optional[str] = None) -> Dict[str, Any]:
    resolved = path.resolve()
    rel = None
    if repo_root is not None:
        try:
            rel = resolved.relative_to(repo_root.resolve()).as_posix()
        except Exception:
            rel = None
    return {
        "role": role,
        "path": str(resolved),
        "repo_relpath": rel,
        "size_bytes": resolved.stat().st_size if resolved.exists() else None,
        "sha256": sha256_file(resolved) if resolved.exists() else None,
    }


def extract_references(value: Any) -> Set[str]:
    refs: Set[str] = set()
    if isinstance(value, str):
        if value.startswith("$"):
            refs.add(value[1:].split(".", 1)[0])
    elif isinstance(value, list):
        for item in value:
            refs.update(extract_references(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.update(extract_references(item))
    return refs


def load_json(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    elif raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return json.loads(text)


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_schema(schema_ref: Any, base_dir: Path) -> Dict[str, Any]:
    if isinstance(schema_ref, dict):
        return schema_ref
    if isinstance(schema_ref, str):
        path = Path(schema_ref)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        return json.loads(path.read_text(encoding="utf-8"))
    raise TypeError("schema must be an object or path string")


def normalize_proof(proof: Dict[str, Any], default_validator: str = "", default_target: str = "") -> Dict[str, Any]:
    return {
        "validator": str(proof.get("validator") or default_validator or "unknown"),
        "target": str(proof.get("target") or default_target or "unknown"),
        "status": str(proof.get("status") or "unknown"),
        "message": str(proof.get("message") or ""),
        "evidence": proof.get("evidence"),
        # Proof timestamps are optional; do not auto-inject wall-clock time.
        "timestamp": str(proof["timestamp"]) if "timestamp" in proof and proof["timestamp"] is not None else None,
    }



def build_proof(validator: str, target: str, status: str, message: str, evidence: Any) -> Dict[str, Any]:
    return normalize_proof(
        {
            "validator": validator,
            "target": target,
            "status": status,
            "message": message,
            "evidence": evidence,
            "timestamp": utc_now(),
        }
    )



def extract_proofs(value: Any, default_validator: str = "", default_target: str = "") -> List[Dict[str, Any]]:
    proofs: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("proof"), dict):
            proofs.append(normalize_proof(value["proof"], default_validator, default_target))
        if isinstance(value.get("proofs"), list):
            for item in value["proofs"]:
                if isinstance(item, dict):
                    proofs.append(normalize_proof(item, default_validator, default_target))
    return proofs



def build_proof_summary(proofs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    items = [normalize_proof(dict(item)) for item in proofs]
    passed = sum(1 for item in items if item["status"] == "passed")
    failed = sum(1 for item in items if item["status"] == "failed")
    other = len(items) - passed - failed
    by_validator: Dict[str, int] = defaultdict(int)
    for item in items:
        by_validator[item["validator"]] += 1
    return {
        "total": len(items),
        "passed": passed,
        "failed": failed,
        "other": other,
        "by_validator": dict(sorted(by_validator.items())),
        "items": items,
        "nondeterministic_fields": ["items[].timestamp"],
    }


class RuneContext:
    def __init__(
        self,
        spell: Spell,
        capabilities: Sequence[str],
        approvals: Set[str],
        simulate: bool,
        artifact_dir: Path,
        policy: Dict[str, Any],
    ) -> None:
        self.spell = spell
        self.capabilities = set(capabilities)
        self.approvals = approvals
        self.simulate = simulate
        self.policy = policy
        self.execution_id = str(uuid.uuid4())
        self.artifact_dir = artifact_dir
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.values: Dict[str, Any] = {"inputs": spell.inputs}
        self.step_meta: Dict[str, Dict[str, Any]] = {}
        self.trace_events: List[TraceEvent] = []
        self.compensation_events: List[CompensationEvent] = []
        self.executed_steps: List[str] = []
        self._mcp_clients: List[MCPClient] = []
        self.proofs: List[Dict[str, Any]] = []
        # v0.9 capability usage events (raw; normalized in diffable witnesses)
        self.capability_events: List[Dict[str, Any]] = []
        self.reviewed_capability_envelope: Optional[Set[str]] = None
        self.enforce_review_bundle: bool = False
        self.capability_denials: List[Dict[str, Any]] = []

    def record_capability_event(
        self,
        *,
        kind: str,
        step_id: Optional[str] = None,
        rune: Optional[str] = None,
        target: Any = None,
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        if kind not in CAPABILITY_KINDS:
            # Keep machine-readable but do not crash runtime if taxonomy drifts.
            pass
        declared = None
        if self.reviewed_capability_envelope is not None:
            declared = kind in self.reviewed_capability_envelope
        event: Dict[str, Any] = {
            "kind": kind,
            "step_id": step_id,
            "rune": rune,
            "target": target,
            "declared_by_review": declared,
        }
        if detail:
            event["detail"] = detail
        self.capability_events.append(event)

    def record_capability_denial(
        self,
        *,
        kind: str,
        step_id: Optional[str],
        rune: Optional[str],
        target: Any,
        reason: str,
        source: str,
    ) -> None:
        denial: Dict[str, Any] = {
            "kind": kind,
            "step_id": step_id,
            "rune": rune,
            "target": target,
            "reason": reason,
            "source": source,
            "declared_by_review": (kind in self.reviewed_capability_envelope) if self.reviewed_capability_envelope is not None else None,
        }
        self.capability_denials.append(denial)

    def add_mcp_client(self, client: "MCPClient") -> None:
        self._mcp_clients.append(client)

    def close(self) -> None:
        for client in self._mcp_clients:
            client.close()
        self._mcp_clients.clear()

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            if value.startswith("$"):
                return self._resolve_ref(value[1:])
            return value
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        return value

    def _resolve_ref(self, key: str) -> Any:
        parts = key.split(".")
        current: Any = self.values[parts[0]]
        for part in parts[1:]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                raise KeyError(f"Unknown reference: ${key}")
        return current

    def inherited_confidence_for(self, step: Step) -> float:
        deps = list(step.requires) + sorted(extract_references(step.args))
        deps = [dep for dep in deps if dep != "inputs" and dep in self.step_meta]
        return min((self.step_meta[dep]["confidence"] for dep in deps), default=1.0)

    def record_step_meta(self, step: Step, confidence: float, uncertainty: Optional[str]) -> None:
        bounded = round(max(0.0, min(1.0, float(confidence))), 4)
        self.step_meta[step.step_id] = {
            "confidence": bounded,
            "entropy": round(1.0 - bounded, 4),
            "uncertainty": uncertainty,
        }

    def attach_step_proofs(self, step: Step, proofs: List[Dict[str, Any]]) -> None:
        if step.step_id not in self.step_meta:
            self.step_meta[step.step_id] = {}
        self.step_meta[step.step_id]["proofs"] = proofs

    def record_proof(self, proof: Dict[str, Any]) -> None:
        self.proofs.append(normalize_proof(proof))

    def preview(self, value: Any, limit: int = 260) -> str:
        text = repr(value)
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def rel_path(self, raw: str) -> Path:
        path = Path(raw)
        return path if path.is_absolute() else (self.spell.source_path.parent / path).resolve()

    def maybe_path(self, raw: str) -> Path:
        path = Path(raw)
        if path.is_absolute():
            return path
        return (self.spell.source_path.parent / path).resolve()

    def write_text(self, path: Path, text: str) -> Dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {
            "path": str(path),
            "mode": "text",
            "bytes": len(text.encode("utf-8")),
            "sha256": digest,
            "status": "written",
        }

    def write_json(self, path: Path, payload: Any) -> Dict[str, Any]:
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, ensure_ascii=False)
        return self.write_text(path, text)


class MCPClient:
    def __init__(self, cmd: Sequence[str]) -> None:
        self.cmd = list(cmd)
        self._id = 0
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "axiomurgy", "version": VERSION},
            },
        )
        self.notify("notifications/initialized", {})

    def _write(self, payload: Dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _read(self) -> Dict[str, Any]:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise StepExecutionError("MCP server closed unexpectedly")
        return json.loads(line)

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._id += 1
        self._write({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        response = self._read()
        if "error" in response:
            raise StepExecutionError(f"MCP error for {method}: {response['error']}")
        return response.get("result", {})

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def list_resources(self) -> List[Dict[str, Any]]:
        return list(self.request("resources/list", {}).get("resources", []))

    def read_resource(self, uri: str) -> List[Dict[str, Any]]:
        return list(self.request("resources/read", {"uri": uri}).get("contents", []))

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)



def parse_step(raw: Dict[str, Any]) -> Step:
    return Step(
        step_id=raw["id"],
        rune=raw["rune"],
        effect=raw.get("effect", "transform"),
        args=raw.get("args", {}),
        requires=list(raw.get("requires", [])),
        output_schema=raw.get("output_schema"),
        confidence=raw.get("confidence"),
        compensates=raw.get("compensates"),
        description=raw.get("description", ""),
    )



def load_spell(path: Path) -> Spell:
    document = load_json(path)
    jsonschema.validate(instance=document, schema=load_json(DEFAULT_SCHEMA_PATH))
    return Spell(
        name=document["spell"],
        intent=document["intent"],
        inputs=document.get("inputs", {}),
        constraints=document.get("constraints", {}),
        graph=[parse_step(s) for s in document.get("graph", [])],
        rollback=[parse_step(s) for s in document.get("rollback", [])],
        witness=document.get("witness", {"record": True, "format": "prov-like"}),
        source_path=path.resolve(),
    )



def load_spellbook(path: Path) -> Spellbook:
    manifest_path = path / "spellbook.json" if path.is_dir() else path
    if manifest_path.name != "spellbook.json":
        raise SpellValidationError(f"Expected a spellbook directory or spellbook.json, got: {path}")
    document = load_json(manifest_path)
    jsonschema.validate(instance=document, schema=load_json(DEFAULT_SPELLBOOK_SCHEMA_PATH))
    raw_entrypoints = document.get("entrypoints", {})
    entrypoints: Dict[str, Dict[str, Any]] = {}
    for name, value in raw_entrypoints.items():
        if isinstance(value, str):
            entrypoints[name] = {"spell": value}
        elif isinstance(value, dict) and isinstance(value.get("spell"), str):
            entrypoints[name] = dict(value)
        else:
            raise SpellValidationError(f"Invalid spellbook entrypoint '{name}'")
    return Spellbook(
        name=str(document["name"]),
        version=str(document["version"]),
        description=str(document.get("description", "")),
        entrypoints=entrypoints,
        required_capabilities=[str(item) for item in document.get("required_capabilities", [])],
        default_policy=document.get("default_policy"),
        validators=[str(item) for item in document.get("validators", [])],
        artifacts_dir=document.get("artifacts_dir"),
        default_entrypoint=document.get("default_entrypoint"),
        source_path=manifest_path.resolve(),
    )



def resolve_run_target(
    target: Path,
    entrypoint: Optional[str],
    policy_override: Optional[Path],
    artifact_override: Optional[Path],
) -> ResolvedRunTarget:
    if target.is_dir() and (target / "spellbook.json").exists():
        spellbook = load_spellbook(target)
    elif target.name == "spellbook.json":
        spellbook = load_spellbook(target)
    else:
        spell = load_spell(target)
        policy_path = policy_override.resolve() if policy_override else DEFAULT_POLICY_PATH
        artifact_dir = artifact_override.resolve() if artifact_override else DEFAULT_ARTIFACT_DIR
        return ResolvedRunTarget(spell=spell, policy_path=policy_path, artifact_dir=artifact_dir)

    entrypoint_name = entrypoint or spellbook.default_entrypoint
    if not entrypoint_name:
        if len(spellbook.entrypoints) == 1:
            entrypoint_name = next(iter(spellbook.entrypoints))
        else:
            raise SpellValidationError(
                f"Spellbook '{spellbook.name}' has multiple entrypoints. Pass --entrypoint with one of: {sorted(spellbook.entrypoints)}"
            )
    if entrypoint_name not in spellbook.entrypoints:
        raise SpellValidationError(
            f"Unknown entrypoint '{entrypoint_name}' for spellbook '{spellbook.name}'. Valid entrypoints: {sorted(spellbook.entrypoints)}"
        )

    entry = spellbook.entrypoints[entrypoint_name]
    spell_path = (spellbook.source_path.parent / entry["spell"]).resolve()
    spell = load_spell(spell_path)
    merged_caps = set(spell.constraints.get("required_capabilities", []))
    merged_caps.update(spellbook.required_capabilities)
    merged_caps.update(entry.get("required_capabilities", []))
    if merged_caps:
        spell.constraints["required_capabilities"] = sorted(merged_caps)

    if policy_override:
        policy_path = policy_override.resolve()
    elif entry.get("policy"):
        policy_path = (spellbook.source_path.parent / str(entry["policy"])).resolve()
    elif spellbook.default_policy:
        policy_path = (spellbook.source_path.parent / spellbook.default_policy).resolve()
    else:
        policy_path = DEFAULT_POLICY_PATH

    if artifact_override:
        artifact_dir = artifact_override.resolve()
    elif entry.get("artifacts_dir"):
        artifact_dir = (spellbook.source_path.parent / str(entry["artifacts_dir"])).resolve()
    elif spellbook.artifacts_dir:
        artifact_dir = (spellbook.source_path.parent / spellbook.artifacts_dir).resolve()
    else:
        artifact_dir = DEFAULT_ARTIFACT_DIR

    return ResolvedRunTarget(
        spell=spell,
        policy_path=policy_path,
        artifact_dir=artifact_dir,
        spellbook=spellbook,
        entrypoint=entrypoint_name,
    )



def check_spell_capabilities(spell: Spell, capabilities: Sequence[str]) -> None:
    needed = set(spell.constraints.get("required_capabilities", []))
    missing = sorted(needed - set(capabilities))
    if missing:
        raise CapabilityError(f"Missing spell-level capabilities: {missing}")



def compile_plan(spell: Spell) -> List[Step]:
    step_map = {step.step_id: step for step in spell.graph}
    if len(step_map) != len(spell.graph):
        raise SpellValidationError("Duplicate step ids in graph")
    deps: Dict[str, Set[str]] = {}
    rev: Dict[str, Set[str]] = defaultdict(set)
    order = {step.step_id: idx for idx, step in enumerate(spell.graph)}
    for step in spell.graph:
        need = set(step.requires)
        need.update(ref for ref in extract_references(step.args) if ref != "inputs")
        unknown = need - set(step_map)
        if unknown:
            raise SpellValidationError(f"Step '{step.step_id}' depends on unknown steps: {sorted(unknown)}")
        deps[step.step_id] = need
        for dep in need:
            rev[dep].add(step.step_id)
    heap: List[Tuple[int, str]] = []
    queued: Set[str] = set()
    for step in spell.graph:
        if not deps[step.step_id]:
            heapq.heappush(heap, (order[step.step_id], step.step_id))
            queued.add(step.step_id)
    out: List[str] = []
    while heap:
        _idx, current = heapq.heappop(heap)
        out.append(current)
        for child in sorted(rev.get(current, []), key=lambda s: order[s]):
            deps[child].discard(current)
            if not deps[child] and child not in queued and child not in out:
                heapq.heappush(heap, (order[child], child))
                queued.add(child)
    if len(out) != len(spell.graph):
        raise SpellValidationError("Cycle detected in spell graph")
    return [step_map[s] for s in out]



def rule_matches(step: Step, spell_risk: str, rule: Dict[str, Any]) -> bool:
    if "effect" in rule and step.effect not in set(rule.get("effect", [])):
        return False
    if "rune" in rule and step.rune not in set(rule.get("rune", [])):
        return False
    if "min_risk" in rule and RISK_ORDER.get(spell_risk, 0) < RISK_ORDER.get(str(rule.get("min_risk", "low")), 0):
        return False
    return True



def evaluate_policy_static(
    spell: Spell,
    policy: Dict[str, Any],
    approvals: Set[str],
    simulate: bool,
    step: Step,
) -> PolicyDecision:
    spell_risk = str(spell.constraints.get("risk", "low"))
    decision = PolicyDecision()
    for rule in policy.get("deny", []):
        if rule_matches(step, spell_risk, rule):
            decision.allowed = False
            decision.approved = False
            decision.reasons.append(str(rule.get("reason", "Denied by policy.")))
    if simulate and step.effect == "write":
        decision.simulated = True
        decision.requires_approval = False
        decision.approved = True
        decision.reasons.append("Simulate mode suppresses external write side effects.")
        return decision
    if step.effect in set(spell.constraints.get("requires_approval_for", [])):
        decision.requires_approval = True
        decision.reasons.append("Spell constraints require approval for this effect.")
    for rule in policy.get("requires_approval", []):
        if rule_matches(step, spell_risk, rule):
            decision.requires_approval = True
            decision.reasons.append(str(rule.get("reason", "Approval required by policy.")))
    decision.approved = (step.step_id in approvals or "all" in approvals) if decision.requires_approval else True
    return decision



def evaluate_policy(ctx: RuneContext, step: Step) -> PolicyDecision:
    ctx.record_capability_event(
        kind="policy.evaluate",
        step_id=step.step_id,
        rune=step.rune,
        target={"effect": step.effect},
    )
    return evaluate_policy_static(ctx.spell, ctx.policy, ctx.approvals, ctx.simulate, step)



def resolve_static_reference(key: str, values: Dict[str, Any]) -> Any:
    parts = key.split(".")
    current: Any = values[parts[0]]
    for part in parts[1:]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(f"Unknown static reference: ${key}")
    return current



def resolve_static_value(value: Any, values: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value.startswith("$"):
            try:
                return resolve_static_reference(value[1:], values)
            except KeyError:
                return value
        return value
    if isinstance(value, list):
        return [resolve_static_value(item, values) for item in value]
    if isinstance(value, dict):
        return {key: resolve_static_value(item, values) for key, item in value.items()}
    return value



def step_dependencies(step: Step) -> List[str]:
    return sorted(set(step.requires) | {ref for ref in extract_references(step.args) if ref != "inputs"})



def summarize_write_target(step: Step, static_args: Dict[str, Any]) -> Any:
    if step.rune == "gate.file_write":
        return static_args.get("path")
    if step.rune == "gate.emit":
        return static_args.get("target", "stdout")
    if step.rune == "gate.openapi_call":
        return {
            "spec": static_args.get("spec"),
            "operationId": static_args.get("operationId"),
            "arguments": static_args.get("arguments", {}),
        }
    if step.rune == "gate.mcp_call_tool":
        return {
            "server_cmd": static_args.get("server_cmd"),
            "tool": static_args.get("name"),
            "arguments": static_args.get("arguments", {}),
        }
    return None



def external_call_kind(step: Step) -> Optional[str]:
    if step.rune == "gate.openapi_call":
        return "openapi"
    if step.rune == "gate.mcp_call_tool":
        return "mcp"
    return None


def capability_kinds_for_step(step: Step) -> Set[str]:
    """
    Deterministic, conservative capability classification for review bundles.
    This is independent of the coarse runtime rune gate (read/write/verify/etc).
    """
    rune = step.rune
    kinds: Set[str] = set()

    # Runtime-internal invariants: execution always evaluates policy and emits witnesses.
    kinds.add("policy.evaluate")
    kinds.add("witness.emit")

    # Filesystem read surfaces
    if rune in {"mirror.read"}:
        kinds.add("filesystem.read")

    # MCP read surfaces
    if rune in {"mirror.mcp_read_resources"}:
        kinds.add("mcp.resource.read")

    # Filesystem write surfaces
    if rune in {"gate.file_write", "gate.archive", "gate.emit"}:
        kinds.add("filesystem.write")

    # Network surfaces
    if rune == "gate.openapi_call":
        kinds.add("network.http")

    # MCP tool call surfaces
    if rune == "gate.mcp_call_tool":
        kinds.add("mcp.tool.call")

    # Any explicit write effect conservatively implies filesystem.write unless already covered.
    # (This keeps the envelope meaningful even if new write runes are introduced without mapping.)
    if step.effect == "write":
        kinds.add("filesystem.write")

    return kinds


def capability_manifest_for_plan(plan: List[Step]) -> Dict[str, Any]:
    per_step: List[Dict[str, Any]] = []
    required: Set[str] = set()
    for step in plan:
        kinds = capability_kinds_for_step(step)
        required.update(kinds)
        per_step.append(
            {
                "step_id": step.step_id,
                "rune": step.rune,
                "effect": step.effect,
                "kinds": sorted(kinds),
            }
        )
    required_sorted = sorted(required)
    return {
        "kinds_catalog": sorted(CAPABILITY_KINDS),
        "required": required_sorted,
        "per_step": per_step,
        # Default reviewed envelope is the required set for this plan.
        "envelope": {"kinds": required_sorted},
    }



def build_approval_manifest(
    resolved: ResolvedRunTarget,
    steps: List[Dict[str, Any]],
    required_approvals: List[Dict[str, Any]],
    write_steps: List[Dict[str, Any]],
    external_calls: List[Dict[str, Any]],
) -> Dict[str, Any]:
    input_classification = classify_input_manifest(resolved.spell)
    plan = compile_plan(resolved.spell)
    capabilities = capability_manifest_for_plan(plan)
    return {
        "spell": resolved.spell.name,
        "spellbook": {
            "name": resolved.spellbook.name,
            "version": resolved.spellbook.version,
            "entrypoint": resolved.entrypoint,
            "path": str(resolved.spellbook.source_path),
        } if resolved.spellbook is not None else None,
        "policy_path": str(resolved.policy_path),
        "artifact_dir": str(resolved.artifact_dir),
        "risk": str(resolved.spell.constraints.get("risk", "low")),
        "required_approvals": required_approvals,
        "write_steps": write_steps,
        "external_calls": external_calls,
        "input_manifest": input_classification["summary"],
        "capabilities": {"required": capabilities.get("required", []), "envelope": capabilities.get("envelope", {})},
        "simulate_recommendation": bool(required_approvals or external_calls or write_steps),
        "ordered_steps": [
            {
                "index": item["index"],
                "step_id": item["step_id"],
                "rune": item["rune"],
                "effect": item["effect"],
            }
            for item in steps
        ],
    }



def build_plan_summary(
    resolved: ResolvedRunTarget,
    approvals: Optional[Set[str]] = None,
    simulate: bool = False,
) -> Dict[str, Any]:
    approvals = approvals or set()
    policy = load_json(resolved.policy_path)
    repo_root = ROOT
    fingerprints = compute_spell_fingerprints(resolved.spell, resolved.policy_path, repo_root=repo_root)
    if resolved.spellbook is not None:
        fingerprints["spellbook"] = compute_spellbook_fingerprints(resolved, repo_root=repo_root)
    plan = compile_plan(resolved.spell)
    capabilities = capability_manifest_for_plan(plan)
    static_values = {"inputs": resolved.spell.inputs}
    step_rows: List[Dict[str, Any]] = []
    required_approvals: List[Dict[str, Any]] = []
    write_steps: List[Dict[str, Any]] = []
    external_calls: List[Dict[str, Any]] = []
    for index, step in enumerate(plan, start=1):
        static_args = resolve_static_value(step.args, static_values)
        decision = evaluate_policy_static(resolved.spell, policy, approvals, simulate, step)
        row = {
            "index": index,
            "step_id": step.step_id,
            "rune": step.rune,
            "effect": step.effect,
            "description": step.description,
            "depends_on": step_dependencies(step),
            "references": sorted(extract_references(step.args)),
            "args": static_args,
            "policy": {
                "allowed": decision.allowed,
                "requires_approval": decision.requires_approval,
                "approved": decision.approved,
                "simulated": decision.simulated,
                "reasons": decision.reasons,
            },
        }
        target = summarize_write_target(step, static_args if isinstance(static_args, dict) else {})
        if target is not None:
            row["planned_target"] = target
        step_rows.append(row)
        if step.effect == "write":
            write_entry = {
                "step_id": step.step_id,
                "rune": step.rune,
                "effect": step.effect,
                "target": target,
                "requires_approval": decision.requires_approval,
                "approved": decision.approved,
            }
            write_steps.append(write_entry)
        kind = external_call_kind(step)
        if kind is not None:
            external_calls.append(
                {
                    "step_id": step.step_id,
                    "kind": kind,
                    "rune": step.rune,
                    "target": target,
                    "requires_approval": decision.requires_approval,
                    "approved": decision.approved,
                }
            )
        if decision.requires_approval:
            required_approvals.append(
                {
                    "step_id": step.step_id,
                    "rune": step.rune,
                    "effect": step.effect,
                    "reasons": decision.reasons,
                    "granted": decision.approved,
                }
            )
    manifest = build_approval_manifest(resolved, step_rows, required_approvals, write_steps, external_calls)
    out = {
        "mode": "plan",
        "spell": {
            "name": resolved.spell.name,
            "intent": resolved.spell.intent,
            "path": str(resolved.spell.source_path),
            "risk": str(resolved.spell.constraints.get("risk", "low")),
            "required_capabilities": list(resolved.spell.constraints.get("required_capabilities", [])),
        },
        "policy_path": str(resolved.policy_path),
        "artifact_dir": str(resolved.artifact_dir),
        "fingerprints": fingerprints,
        "capabilities": capabilities,
        "steps": step_rows,
        "write_steps": write_steps,
        "required_approvals": required_approvals,
        "external_calls": external_calls,
        "manifest": manifest,
    }
    if resolved.spellbook is not None:
        out["spellbook"] = {
            "name": resolved.spellbook.name,
            "version": resolved.spellbook.version,
            "entrypoint": resolved.entrypoint,
            "path": str(resolved.spellbook.source_path),
        }
    return out



def describe_target(resolved: ResolvedRunTarget) -> Dict[str, Any]:
    repo_root = ROOT
    fingerprints = compute_spell_fingerprints(resolved.spell, resolved.policy_path, repo_root=repo_root)
    if resolved.spellbook is not None:
        fingerprints["spellbook"] = compute_spellbook_fingerprints(resolved, repo_root=repo_root)
    plan = compile_plan(resolved.spell)
    capabilities = capability_manifest_for_plan(plan)
    description = {
        "mode": "describe",
        "kind": "spellbook" if resolved.spellbook is not None else "spell",
        "target": str(resolved.spellbook.source_path if resolved.spellbook is not None else resolved.spell.source_path),
        "spell": {
            "name": resolved.spell.name,
            "intent": resolved.spell.intent,
            "path": str(resolved.spell.source_path),
            "risk": str(resolved.spell.constraints.get("risk", "low")),
            "required_capabilities": list(resolved.spell.constraints.get("required_capabilities", [])),
            "required_approval_for": list(resolved.spell.constraints.get("requires_approval_for", [])),
            "witness": resolved.spell.witness,
        },
        "policy_path": str(resolved.policy_path),
        "artifact_dir": str(resolved.artifact_dir),
        "fingerprints": fingerprints,
        "capabilities": capabilities,
    }
    if resolved.spellbook is not None:
        description["spellbook"] = {
            "name": resolved.spellbook.name,
            "version": resolved.spellbook.version,
            "description": resolved.spellbook.description,
            "path": str(resolved.spellbook.source_path),
            "default_entrypoint": resolved.spellbook.default_entrypoint,
            "resolved_entrypoint": resolved.entrypoint,
            "required_capabilities": resolved.spellbook.required_capabilities,
            "validators": resolved.spellbook.validators,
            "entrypoints": resolved.spellbook.entrypoints,
        }
    return description



def build_lint_issue(severity: str, code: str, message: str, path: str) -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "path": path,
    }



def iter_schema_issues(instance: Any, schema: Dict[str, Any], path_prefix: str) -> List[Dict[str, Any]]:
    validator = jsonschema.Draft202012Validator(schema)
    issues: List[Dict[str, Any]] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.path)):
        rendered_path = "/".join(str(part) for part in error.path)
        full_path = path_prefix if not rendered_path else f"{path_prefix}/{rendered_path}"
        issues.append(build_lint_issue("error", "schema", error.message, full_path))
    return issues



def lint_spell_file(
    path: Path,
    policy_path: Optional[Path] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    target_label = label or str(path)
    try:
        raw = load_json(path)
    except json.JSONDecodeError as exc:
        errors.append(build_lint_issue("error", "json", str(exc), target_label))
        return {"target": target_label, "kind": "spell", "ok": False, "errors": errors, "warnings": warnings}
    errors.extend(iter_schema_issues(raw, load_json(DEFAULT_SCHEMA_PATH), target_label))
    if errors:
        return {"target": target_label, "kind": "spell", "ok": False, "errors": errors, "warnings": warnings}
    try:
        spell = load_spell(path)
    except Exception as exc:  # pragma: no cover - defensive fallback after schema checks
        errors.append(build_lint_issue("error", "load_spell", str(exc), target_label))
        return {"target": target_label, "kind": "spell", "ok": False, "errors": errors, "warnings": warnings}

    graph_ids = [step.step_id for step in spell.graph]
    rollback_ids = [step.step_id for step in spell.rollback]
    for duplicate in sorted({item for item in graph_ids if graph_ids.count(item) > 1}):
        errors.append(build_lint_issue("error", "duplicate_step_id", f"Duplicate graph step id: {duplicate}", f"{target_label}/graph"))
    for duplicate in sorted({item for item in rollback_ids if rollback_ids.count(item) > 1}):
        errors.append(build_lint_issue("error", "duplicate_rollback_step_id", f"Duplicate rollback step id: {duplicate}", f"{target_label}/rollback"))

    all_graph_ids = {step.step_id for step in spell.graph}
    for section_name, steps in (("graph", spell.graph), ("rollback", spell.rollback)):
        for step in steps:
            if step.rune not in REGISTRY._handlers:
                errors.append(
                    build_lint_issue(
                        "error",
                        "unknown_rune",
                        f"Unknown rune '{step.rune}'",
                        f"{target_label}/{section_name}/{step.step_id}",
                    )
                )
            if isinstance(step.output_schema, str):
                schema_path = (spell.source_path.parent / step.output_schema).resolve()
                if not schema_path.exists():
                    errors.append(
                        build_lint_issue(
                            "error",
                            "missing_output_schema",
                            f"Output schema path not found: {schema_path}",
                            f"{target_label}/{section_name}/{step.step_id}/output_schema",
                        )
                    )
            if section_name == "rollback" and step.compensates not in all_graph_ids:
                errors.append(
                    build_lint_issue(
                        "error",
                        "unknown_compensation_target",
                        f"Rollback step compensates unknown graph step: {step.compensates}",
                        f"{target_label}/{section_name}/{step.step_id}/compensates",
                    )
                )
    try:
        compile_plan(spell)
    except SpellValidationError as exc:
        errors.append(build_lint_issue("error", "graph", str(exc), f"{target_label}/graph"))

    effective_policy_path = (policy_path or DEFAULT_POLICY_PATH).resolve()
    policy = None
    if effective_policy_path.exists():
        try:
            policy = load_json(effective_policy_path)
        except Exception as exc:
            errors.append(build_lint_issue("error", "policy_json", str(exc), f"{target_label}/policy"))
    else:
        errors.append(build_lint_issue("error", "missing_policy", f"Policy path not found: {effective_policy_path}", f"{target_label}/policy"))
    if policy is not None:
        for step in spell.graph:
            if step.effect != "write":
                continue
            decision = evaluate_policy_static(spell, policy, set(), False, step)
            if not decision.requires_approval:
                warnings.append(
                    build_lint_issue(
                        "warning",
                        "write_without_approval",
                        f"Write step '{step.step_id}' is not gated by spell constraints or policy approvals.",
                        f"{target_label}/graph/{step.step_id}",
                    )
                )

    return {
        "target": target_label,
        "kind": "spell",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }



def lint_spellbook(path: Path, policy_override: Optional[Path] = None) -> Dict[str, Any]:
    manifest_path = path / "spellbook.json" if path.is_dir() else path
    target_label = str(manifest_path)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    try:
        raw = load_json(manifest_path)
    except json.JSONDecodeError as exc:
        errors.append(build_lint_issue("error", "json", str(exc), target_label))
        return {"target": target_label, "kind": "spellbook", "ok": False, "errors": errors, "warnings": warnings, "entrypoints": {}}
    errors.extend(iter_schema_issues(raw, load_json(DEFAULT_SPELLBOOK_SCHEMA_PATH), target_label))
    if errors:
        return {"target": target_label, "kind": "spellbook", "ok": False, "errors": errors, "warnings": warnings, "entrypoints": {}}
    spellbook = load_spellbook(manifest_path)
    if spellbook.default_entrypoint and spellbook.default_entrypoint not in spellbook.entrypoints:
        errors.append(
            build_lint_issue(
                "error",
                "unknown_default_entrypoint",
                f"default_entrypoint '{spellbook.default_entrypoint}' is not defined in entrypoints",
                f"{target_label}/default_entrypoint",
            )
        )
    if spellbook.default_policy:
        policy_path = (spellbook.source_path.parent / spellbook.default_policy).resolve()
        if not policy_path.exists():
            errors.append(
                build_lint_issue(
                    "error",
                    "missing_default_policy",
                    f"Default policy path not found: {policy_path}",
                    f"{target_label}/default_policy",
                )
            )
    entry_results: Dict[str, Any] = {}
    for name, entry in spellbook.entrypoints.items():
        spell_path = (spellbook.source_path.parent / entry["spell"]).resolve()
        if not spell_path.exists():
            issue = build_lint_issue(
                "error",
                "missing_entrypoint_spell",
                f"Entrypoint spell not found: {spell_path}",
                f"{target_label}/entrypoints/{name}/spell",
            )
            errors.append(issue)
            entry_results[name] = {"ok": False, "errors": [issue], "warnings": []}
            continue
        if entry.get("policy"):
            entry_policy_path = (spellbook.source_path.parent / str(entry["policy"])).resolve()
        elif spellbook.default_policy:
            entry_policy_path = (spellbook.source_path.parent / spellbook.default_policy).resolve()
        else:
            entry_policy_path = policy_override.resolve() if policy_override else DEFAULT_POLICY_PATH.resolve()
        if not entry_policy_path.exists():
            errors.append(
                build_lint_issue(
                    "error",
                    "missing_entrypoint_policy",
                    f"Entrypoint policy path not found: {entry_policy_path}",
                    f"{target_label}/entrypoints/{name}/policy",
                )
            )
        entry_result = lint_spell_file(spell_path, policy_path=entry_policy_path, label=f"{target_label}::entrypoint:{name}")
        entry_results[name] = entry_result
        errors.extend(entry_result["errors"])
        warnings.extend(entry_result["warnings"])
    return {
        "target": target_label,
        "kind": "spellbook",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "entrypoints": entry_results,
    }



def lint_target(target: Path, policy_override: Optional[Path] = None) -> Dict[str, Any]:
    if target.is_dir() and (target / "spellbook.json").exists():
        return lint_spellbook(target, policy_override=policy_override)
    if target.name == "spellbook.json":
        return lint_spellbook(target, policy_override=policy_override)
    return lint_spell_file(target, policy_path=policy_override)


def environment_metadata() -> Dict[str, Any]:
    py = sys.version.split()[0]
    parts = py.split(".")
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else py
    return {
        "axiomurgy_version": VERSION,
        "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        "python": {"version": py, "major_minor": major_minor, "implementation": sys.implementation.name},
        "platform": {"platform": sys.platform},
        "witness_canonical_json": True,
    }


def compare_reviewed_bundle(reviewed: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    diffs: List[Dict[str, Any]] = []

    def diff(path: str, reviewed_value: Any, current_value: Any, severity: str) -> None:
        if reviewed_value == current_value:
            return
        diffs.append({"path": path, "reviewed": reviewed_value, "current": current_value, "severity": severity})

    reviewed_env = reviewed.get("environment", {})
    current_env = current.get("environment", {})
    # Required environment: behavior-affecting and reproducibility-critical
    for key in ["axiomurgy_version", "mcp_protocol_version", "witness_canonical_json"]:
        diff(f"environment.{key}", reviewed_env.get(key), current_env.get(key), "required")
    diff("environment.python.implementation", reviewed_env.get("python", {}).get("implementation"), current_env.get("python", {}).get("implementation"), "required")
    diff("environment.python.major_minor", reviewed_env.get("python", {}).get("major_minor"), current_env.get("python", {}).get("major_minor"), "required")
    diff("environment.platform.platform", reviewed_env.get("platform", {}).get("platform"), current_env.get("platform", {}).get("platform"), "required")
    # Allowlisted noncritical: patch version changes
    diff("environment.python.version", reviewed_env.get("python", {}).get("version"), current_env.get("python", {}).get("version"), "allowlisted")

    reviewed_fps = (reviewed.get("fingerprints") or {}).get("required", {})
    current_fps = (current.get("fingerprints") or {}).get("required", {})
    for key in sorted(set(reviewed_fps) | set(current_fps)):
        diff(f"fingerprints.required.{key}", reviewed_fps.get(key), current_fps.get(key), "required")

    # Spellbook required fingerprints if present
    reviewed_sb = (reviewed.get("fingerprints") or {}).get("spellbook", {}).get("required", {})
    current_sb = (current.get("fingerprints") or {}).get("spellbook", {}).get("required", {})
    for key in sorted(set(reviewed_sb) | set(current_sb)):
        diff(f"fingerprints.spellbook.required.{key}", reviewed_sb.get(key), current_sb.get(key), "required")

    reviewed_unresolved = (
        (((reviewed.get("fingerprints") or {}).get("input_manifest") or {}).get("classification") or {}).get("summary") or {}
    ).get("unresolved_dynamic_present", False)
    current_unresolved = (
        (((current.get("fingerprints") or {}).get("input_manifest") or {}).get("classification") or {}).get("summary") or {}
    ).get("unresolved_dynamic_present", False)
    # Unresolved inputs degrade portability/contract strength; treat as allowlisted => partial.
    diff("fingerprints.input_manifest.classification.summary.unresolved_dynamic_present", reviewed_unresolved, current_unresolved, "allowlisted")

    # v0.9 reviewed capability envelope (backward compatible if missing).
    reviewed_caps = ((reviewed.get("capabilities") or {}).get("envelope") or {}).get("kinds")
    current_caps = ((current.get("capabilities") or {}).get("envelope") or {}).get("kinds")
    if reviewed_caps is None:
        diffs.append(
            {
                "path": "capabilities.envelope.kinds",
                "reviewed": None,
                "current": current_caps,
                "severity": "allowlisted",
                "note": "Reviewed bundle missing capability envelope (v0.8 or earlier); cannot attest overreach from bundle alone.",
            }
        )
    else:
        diff("capabilities.envelope.kinds", reviewed_caps, current_caps, "required")

    required_mismatch = any(item["severity"] == "required" for item in diffs)
    allowlisted_mismatch = any(item["severity"] == "allowlisted" for item in diffs)
    status = "mismatch" if required_mismatch else "partial" if allowlisted_mismatch else "exact"
    return {"status": status, "diffs": diffs, "reviewed": reviewed, "current": current}


def compute_attestation(reviewed_bundle: Dict[str, Any], resolved: ResolvedRunTarget, approvals: Optional[Set[str]] = None) -> Dict[str, Any]:
    current_bundle = build_review_bundle(resolved, approvals=approvals or set())
    cmp = compare_reviewed_bundle(reviewed_bundle, current_bundle)
    status = cmp["status"]
    # Default policy hook: unresolved dynamic inputs => at most partial.
    unresolved_present = (
        (((current_bundle.get("fingerprints") or {}).get("input_manifest") or {}).get("classification") or {}).get("summary") or {}
    ).get("unresolved_dynamic_present", False)
    if unresolved_present and status == "exact":
        status = "partial"
        cmp["diffs"].append(
            {
                "path": "fingerprints.input_manifest.classification.summary.unresolved_dynamic_present",
                "reviewed": None,
                "current": True,
                "severity": "allowlisted",
                "note": "Unresolved dynamic inputs degrade portability; attestation downgraded to partial.",
            }
        )
    return {"status": status, "diffs": cmp["diffs"]}


def build_review_bundle(resolved: ResolvedRunTarget, approvals: Optional[Set[str]] = None) -> Dict[str, Any]:
    approvals = approvals or set()
    describe = describe_target(resolved)
    lint = lint_target(resolved.spellbook.source_path.parent if resolved.spellbook is not None else resolved.spell.source_path)
    plan = build_plan_summary(resolved, approvals=approvals, simulate=False)
    capabilities = plan.get("capabilities") or describe.get("capabilities") or {}
    return {
        "bundle_version": "0.9",
        "environment": environment_metadata(),
        "target": {
            "kind": "spellbook" if resolved.spellbook is not None else "spell",
            "path": str(resolved.spellbook.source_path if resolved.spellbook is not None else resolved.spell.source_path),
            "entrypoint": resolved.entrypoint,
        },
        "describe": describe,
        "lint": lint,
        "plan": plan,
        "approval_manifest": plan.get("manifest"),
        "fingerprints": plan.get("fingerprints"),
        "capabilities": capabilities,
    }


def apply_output_schema(step: Step, value: Any, spell: Spell) -> None:
    if step.output_schema is not None:
        jsonschema.validate(instance=value, schema=load_schema(step.output_schema, spell.source_path.parent))



def run_step(
    ctx: RuneContext,
    step: Step,
    decision: PolicyDecision,
    compensation_for: Optional[str] = None,
) -> RuneOutcome:
    # v1.0 vessel enforcement: preflight predicted capabilities against reviewed envelope.
    if ctx.enforce_review_bundle and ctx.reviewed_capability_envelope is not None:
        predicted = capability_kinds_for_step(step)
        over = sorted(set(predicted) - set(ctx.reviewed_capability_envelope))
        if over:
            kind = over[0]
            payload = {
                "kind": kind,
                "step_id": step.step_id,
                "rune": step.rune,
                "target": summarize_write_target(step, resolve_static_value(step.args, {"inputs": ctx.spell.inputs}) if isinstance(step.args, dict) else {}),
                "reason": f"Denied by vessel: capability '{kind}' not in reviewed envelope.",
                "source": "review_envelope",
                "predicted_overreach": over,
            }
            ctx.record_capability_denial(
                kind=kind,
                step_id=step.step_id,
                rune=step.rune,
                target=payload.get("target"),
                reason=payload["reason"],
                source="review_envelope",
            )
            raise CapabilityDeniedError(payload)

    capability = REGISTRY.required_capability(step.rune)
    if capability not in ctx.capabilities:
        raise CapabilityError(f"Step '{step.step_id}' requires capability '{capability}' for rune '{step.rune}'")
    if not decision.allowed:
        raise PolicyDeniedError("; ".join(decision.reasons) or "Denied by policy")
    if decision.requires_approval and not decision.approved:
        raise ApprovalRequiredError("; ".join(decision.reasons) or "Approval required")
    outcome = REGISTRY.handler_for(step.rune)(ctx, step, ctx.resolve(step.args))
    apply_output_schema(step, outcome.value, ctx.spell)
    confidence = step.confidence if step.confidence is not None else ctx.inherited_confidence_for(step) * outcome.confidence_factor
    ctx.record_step_meta(step, confidence, outcome.uncertainty)
    ctx.values[step.step_id] = outcome.value
    proofs = extract_proofs(outcome.value, default_validator=step.rune, default_target=step.step_id)
    if proofs:
        for proof in proofs:
            ctx.record_proof(proof)
        ctx.attach_step_proofs(step, proofs)
    if compensation_for is None:
        ctx.executed_steps.append(step.step_id)
    return outcome



def build_prov_document(ctx: RuneContext, plan: List[Step]) -> Dict[str, Any]:
    entity: Dict[str, Any] = {
        f"entity:spell:{ctx.spell.name}": {
            "prov:label": ctx.spell.name,
            "axiom:intent": ctx.spell.intent,
            "axiom:source": str(ctx.spell.source_path),
        }
    }
    activity: Dict[str, Any] = {}
    used: Dict[str, Any] = {}
    generated: Dict[str, Any] = {}
    derived: Dict[str, Any] = {}
    step_lookup = {step.step_id: step for step in plan}
    for idx, event in enumerate(ctx.trace_events, start=1):
        entity[f"entity:{event.step_id}:output"] = {
            "prov:label": event.step_id,
            "axiom:output_preview": event.output_preview,
            "axiom:status": event.status,
            "axiom:confidence": event.confidence,
            "axiom:entropy": event.entropy,
            "axiom:proofs": event.proofs,
        }
        activity[f"activity:{event.step_id}"] = {
            "prov:startTime": event.started_at,
            "prov:endTime": event.ended_at,
            "prov:type": event.rune,
            "axiom:status": event.status,
            "axiom:effect": event.effect,
        }
        used[f"used:{idx}"] = {
            "prov:activity": f"activity:{event.step_id}",
            "prov:entity": f"entity:spell:{ctx.spell.name}",
        }
        generated[f"generated:{idx}"] = {
            "prov:entity": f"entity:{event.step_id}:output",
            "prov:activity": f"activity:{event.step_id}",
        }
        step = step_lookup.get(event.step_id)
        if step:
            for dep in sorted(set(step.requires) | {r for r in extract_references(step.args) if r != "inputs"}):
                derived[f"derived:{event.step_id}:{dep}"] = {
                    "prov:generatedEntity": f"entity:{event.step_id}:output",
                    "prov:usedEntity": f"entity:{dep}:output",
                }
    return {
        "prefix": {"prov": "http://www.w3.org/ns/prov#", "axiom": "urn:axiomurgy:"},
        "entity": entity,
        "activity": activity,
        "agent": {
            "agent:axiomurgy": {
                "prov:type": "prov:SoftwareAgent",
                "prov:label": "Axiomurgy runtime",
                "axiom:version": VERSION,
            }
        },
        "used": used,
        "wasGeneratedBy": generated,
        "wasDerivedFrom": derived,
        "axiom:proofs": build_proof_summary(ctx.proofs),
    }


def normalize_trace_for_diff(trace: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(canonical_json(trace))
    for key in ["execution_id", "started_at", "ended_at"]:
        out.pop(key, None)
    for event in out.get("events", []) or []:
        if isinstance(event, dict):
            event.pop("started_at", None)
            event.pop("ended_at", None)
    for item in (out.get("proofs", {}) or {}).get("items", []) or []:
        if isinstance(item, dict):
            item.pop("timestamp", None)
    out = normalize_paths_for_portability(out, repo_root=ROOT)
    out["diff_canonical"] = True
    return out


def normalize_prov_for_diff(prov: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(canonical_json(prov))
    # PROV in this runtime includes times derived from trace events; drop them for diff payloads.
    for activity in (out.get("activity", {}) or {}).values():
        if isinstance(activity, dict):
            activity.pop("prov:startTime", None)
            activity.pop("prov:endTime", None)
    if isinstance(out.get("axiom:proofs"), dict):
        for item in (out.get("axiom:proofs") or {}).get("items", []) or []:
            if isinstance(item, dict):
                item.pop("timestamp", None)
    out = normalize_paths_for_portability(out, repo_root=ROOT)
    out["diff_canonical"] = True
    return out


def normalize_proofs_for_diff(proofs: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(canonical_json(proofs))
    for item in out.get("items", []) or []:
        if isinstance(item, dict):
            item.pop("timestamp", None)
    out = normalize_paths_for_portability(out, repo_root=ROOT)
    out["diff_canonical"] = True
    return out


def _looks_like_path(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if lower.startswith(("http://", "https://", "mcp://", "upload://")):
        return False
    if lower.startswith("file://"):
        return True
    # Windows absolute: C:\ or UNC \\server\share
    if re.match(r"^[a-zA-Z]:[\\\\/]", text) or text.startswith("\\\\"):
        return True
    # POSIX absolute
    if text.startswith("/"):
        return True
    return False


def _portable_path_token(text: str, repo_root: Path) -> str:
    raw = text
    if raw.lower().startswith("file://"):
        raw = raw[7:]
    try:
        p = Path(raw)
    except Exception:
        return "<opaque_path>"
    try:
        resolved = p.resolve()
    except Exception:
        return "<opaque_path>"
    try:
        rel = resolved.relative_to(repo_root.resolve()).as_posix()
        return f"repo:{rel}"
    except Exception:
        return "<opaque_path>"


def normalize_paths_for_portability(value: Any, repo_root: Path) -> Any:
    if isinstance(value, str):
        return _portable_path_token(value, repo_root) if _looks_like_path(value) else value
    if isinstance(value, list):
        return [normalize_paths_for_portability(item, repo_root) for item in value]
    if isinstance(value, dict):
        return {k: normalize_paths_for_portability(v, repo_root) for k, v in value.items()}
    return value



def build_scxml(spell: Spell, plan: List[Step]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<scxml xmlns="http://www.w3.org/2005/07/scxml" version="1.0" initial="state_0" name="{spell.name}">',
    ]
    for idx, step in enumerate(plan):
        target = "done" if idx == len(plan) - 1 else f"state_{idx + 1}"
        lines += [
            f'  <state id="state_{idx}">',
            f'    <onentry><log label="{step.step_id}" expr="\'{step.rune}\'"/></onentry>',
            f'    <transition event="success" target="{target}"/>',
            "  </state>",
        ]
    lines += ["  <final id=\"done\"/>", "</scxml>"]
    return "\n".join(lines)



def export_witnesses(ctx: RuneContext, plan: List[Step], trace: Dict[str, Any]) -> None:
    ctx.record_capability_event(kind="witness.emit", target={"artifact_dir": str(ctx.artifact_dir)})
    prov = build_prov_document(ctx, plan)
    proofs = build_proof_summary(ctx.proofs)
    # Raw witnesses preserve wall-clock timing for debugging.
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.trace.raw.json", canonical_json(trace))
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.prov.raw.json", canonical_json(prov))
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.proofs.raw.json", canonical_json(proofs))
    # Diffable witnesses omit nondeterministic fields.
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.trace.json", canonical_json(normalize_trace_for_diff(trace)))
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.prov.json", canonical_json(normalize_prov_for_diff(prov)))
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.scxml", build_scxml(ctx.spell, plan))
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.proofs.json", canonical_json(normalize_proofs_for_diff(proofs)))



def execute_spell(
    spell: Spell,
    capabilities: Sequence[str],
    approvals: Set[str],
    simulate: bool,
    policy_path: Path,
    artifact_dir: Path,
    reviewed_bundle: Optional[Dict[str, Any]] = None,
    enforce_review_bundle: bool = False,
) -> Dict[str, Any]:
    check_spell_capabilities(spell, capabilities)
    ctx = RuneContext(spell, capabilities, approvals, simulate, artifact_dir, load_json(policy_path))
    ctx.enforce_review_bundle = bool(enforce_review_bundle)
    reviewed_envelope = (((reviewed_bundle or {}).get("capabilities") or {}).get("envelope") or {}).get("kinds")
    if isinstance(reviewed_envelope, list):
        ctx.reviewed_capability_envelope = {str(item) for item in reviewed_envelope}
    fingerprints = compute_spell_fingerprints(spell, policy_path, repo_root=ROOT)
    plan = compile_plan(spell)
    rollback_map = {step.compensates: step for step in spell.rollback if step.compensates}
    started_at = utc_now()
    status = "succeeded"
    block_reason: Optional[str] = None
    block_source: Optional[str] = None
    error_message: Optional[str] = None
    try:
        for step in plan:
            decision = evaluate_policy(ctx, step)
            t0 = utc_now()
            try:
                outcome = run_step(ctx, step, decision)
                meta = ctx.step_meta.get(step.step_id, {})
                proofs = list(meta.get("proofs", []))
                ctx.trace_events.append(
                    TraceEvent(
                        step.step_id,
                        step.rune,
                        step.effect,
                        "succeeded",
                        t0,
                        utc_now(),
                        ctx.resolve(step.args),
                        ctx.preview(outcome.value),
                        None,
                        {
                            "allowed": decision.allowed,
                            "requires_approval": decision.requires_approval,
                            "approved": decision.approved,
                            "simulated": decision.simulated,
                            "reasons": decision.reasons,
                        },
                        confidence=meta.get("confidence"),
                        entropy=meta.get("entropy"),
                        uncertainty=meta.get("uncertainty"),
                        proofs=proofs,
                    )
                )
            except Exception as exc:
                failure_proofs: List[Dict[str, Any]] = []
                if isinstance(exc, ProofFailure):
                    failure_proofs.append(normalize_proof(exc.proof, step.rune, step.step_id))
                    for proof in failure_proofs:
                        ctx.record_proof(proof)
                ctx.trace_events.append(
                    TraceEvent(
                        step.step_id,
                        step.rune,
                        step.effect,
                        "failed",
                        t0,
                        utc_now(),
                        ctx.resolve(step.args),
                        ctx.preview({"error": str(exc)}),
                        str(exc),
                        {
                            "allowed": decision.allowed,
                            "requires_approval": decision.requires_approval,
                            "approved": decision.approved,
                            "simulated": decision.simulated,
                            "reasons": decision.reasons,
                        },
                        confidence=ctx.step_meta.get(step.step_id, {}).get("confidence"),
                        entropy=ctx.step_meta.get(step.step_id, {}).get("entropy"),
                        uncertainty=ctx.step_meta.get(step.step_id, {}).get("uncertainty"),
                        proofs=failure_proofs,
                    )
                )
                raise
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        if isinstance(exc, CapabilityDeniedError):
            block_reason = str(exc)
            block_source = str((exc.payload or {}).get("source") or "review_envelope")
        for step_id in reversed(ctx.executed_steps):
            rollback_step = rollback_map.get(step_id)
            if not rollback_step:
                continue
            t0 = utc_now()
            try:
                outcome = run_step(ctx, rollback_step, PolicyDecision(allowed=True, approved=True, simulated=simulate), compensation_for=step_id)
                ctx.compensation_events.append(
                    CompensationEvent(step_id, rollback_step.rune, "compensated", t0, utc_now(), ctx.preview(outcome.value), None)
                )
                for event in ctx.trace_events:
                    if event.step_id == step_id and event.status == "succeeded":
                        event.compensation_ran = True
                        break
            except Exception as comp_exc:
                ctx.compensation_events.append(
                    CompensationEvent(
                        step_id,
                        rollback_step.rune,
                        "compensation_failed",
                        t0,
                        utc_now(),
                        ctx.preview({"error": str(comp_exc)}),
                        str(comp_exc),
                    )
                )
    trace = {
        "spell": spell.name,
        "intent": spell.intent,
        "execution_id": ctx.execution_id,
        "started_at": started_at,
        "ended_at": utc_now(),
        "status": status,
        "events": [event.__dict__ for event in ctx.trace_events],
        "compensations": [event.__dict__ for event in ctx.compensation_events],
        "inputs": spell.inputs,
        "error": error_message,
        "capability_events": list(ctx.capability_events),
        "capability_denials": list(ctx.capability_denials),
        "proofs": build_proof_summary(ctx.proofs),
        "nondeterministic_fields": ["execution_id", "started_at", "ended_at", "proofs.items[].timestamp"],
    }
    if spell.witness.get("record", True):
        export_witnesses(ctx, plan, trace)
    final_step_id = plan[-1].step_id if status == "succeeded" else None
    final_meta = ctx.step_meta.get(final_step_id or "", {})
    final_value = ctx.values.get(final_step_id) if final_step_id else None
    used_kinds = sorted({str(item.get("kind")) for item in ctx.capability_events if isinstance(item, dict) and item.get("kind")})
    reviewed_kinds = sorted(ctx.reviewed_capability_envelope) if ctx.reviewed_capability_envelope is not None else None
    overreach = sorted(set(used_kinds) - set(reviewed_kinds or [])) if reviewed_kinds is not None else []
    execution_outcome = None
    result = {
        "spell": spell.name,
        "intent": spell.intent,
        "status": status,
        "error": error_message,
        "final_step": final_step_id,
        "final": final_value,
        "final_confidence": final_meta.get("confidence"),
        "final_entropy": final_meta.get("entropy"),
        "fingerprints": fingerprints,
        "trace_path": str(ctx.artifact_dir / f"{spell.name}.trace.json"),
        "prov_path": str(ctx.artifact_dir / f"{spell.name}.prov.json"),
        "scxml_path": str(ctx.artifact_dir / f"{spell.name}.scxml"),
        "proof_path": str(ctx.artifact_dir / f"{spell.name}.proofs.json"),
        "proofs": build_proof_summary(ctx.proofs),
        "execution_id": ctx.execution_id,
        "capabilities": {
            "used": used_kinds,
            "reviewed_envelope": reviewed_kinds,
            "overreach": overreach,
        },
        "execution_outcome": execution_outcome,
        "blocked": {"reason": block_reason, "source": block_source} if block_reason else None,
    }
    ctx.close()
    return result



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
    return parser.parse_args(argv)



def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"ERROR: File not found: {target}")
        return 2
    if args.manifest_out and not (args.plan or args.review_bundle):
        print("ERROR: --manifest-out can only be used with --plan or --review-bundle")
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
        if args.lint:
            result = lint_target(target, policy_override=policy_override)
        else:
            resolved = resolve_run_target(target, args.entrypoint, policy_override, artifact_override)
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
                result = build_review_bundle(resolved, approvals=set(args.approve))
                if args.manifest_out:
                    manifest_path = Path(args.manifest_out).resolve()
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(json_dumps(result["approval_manifest"]), encoding="utf-8")
                    result["manifest_path"] = str(manifest_path)
            elif args.plan:
                result = build_plan_summary(resolved, approvals=set(args.approve), simulate=False)
                if args.manifest_out:
                    manifest_path = Path(args.manifest_out).resolve()
                    manifest_path.parent.mkdir(parents=True, exist_ok=True)
                    manifest_path.write_text(json_dumps(result["manifest"]), encoding="utf-8")
                    result["manifest_path"] = str(manifest_path)
            else:
                capabilities = {"read", "memory", "reason", "transform", "verify", "approve", "simulate", "write"}
                capabilities.update(args.capability)
                reviewed_in = load_json(Path(args.review_bundle_in).resolve()) if args.review_bundle_in else None
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
