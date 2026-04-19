"""Describe, lint, and environment metadata workflows."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema

from .legacy import (
    DEFAULT_POLICY_PATH,
    MCP_PROTOCOL_VERSION,
    ResolvedRunTarget,
    SpellValidationError,
    VERSION,
)
from .planning import (
    capability_manifest_for_plan,
    compile_plan,
    evaluate_policy_static,
    load_json,
    load_spell,
    load_spellbook,
)
from .runes import REGISTRY
from .util import DEFAULT_SCHEMA_PATH, DEFAULT_SPELLBOOK_SCHEMA_PATH, ROOT
from .fingerprint import compute_spell_fingerprints, compute_spellbook_fingerprints
from .culture import culture_hints_for_describe

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
    ch = culture_hints_for_describe()
    if ch is not None:
        description["culture"] = ch
    from .reasoning_bundle import attach_reasoning_to_describe

    attach_reasoning_to_describe(description, resolved)
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
