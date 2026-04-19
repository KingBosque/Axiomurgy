"""Spell loading, planning, policy, and capability manifests."""

from __future__ import annotations

import heapq
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import jsonschema

from .fingerprint import (
    classify_input_manifest,
    compute_spell_fingerprints,
    compute_spellbook_fingerprints,
)
from .legacy import (
    CAPABILITY_KINDS,
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_POLICY_PATH,
    CapabilityError,
    PolicyDecision,
    ResolvedRunTarget,
    Spell,
    Spellbook,
    SpellValidationError,
    Step,
    RISK_ORDER,
)
from .util import (
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SPELLBOOK_SCHEMA_PATH,
    ROOT,
    extract_references,
    load_json,
)

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
    *,
    vermyth_program: bool = False,
    vermyth_validate: bool = False,
    vermyth_recommendations: bool = False,
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
    if vermyth_program or vermyth_validate or vermyth_recommendations:
        from . import vermyth_integration

        vermyth_integration.enrich_plan_output(
            out,
            resolved,
            vermyth_program=vermyth_program,
            vermyth_validate=vermyth_validate,
            vermyth_recommendations=vermyth_recommendations,
        )
    from .reasoning_bundle import attach_reasoning_to_plan

    attach_reasoning_to_plan(out, resolved)
    return out
