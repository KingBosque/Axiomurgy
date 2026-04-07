#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import jsonschema
import requests
import yaml

VERSION = "0.5.0"
ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY_PATH = ROOT / "policies" / "default.policy.json"
DEFAULT_SCHEMA_PATH = ROOT / "spell.schema.json"
DEFAULT_SPELLBOOK_SCHEMA_PATH = ROOT / "spellbook.schema.json"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts"
MCP_PROTOCOL_VERSION = "2025-11-25"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
HTTP_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


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
    return json.loads(path.read_text(encoding="utf-8"))


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
        "timestamp": str(proof.get("timestamp") or utc_now()),
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
        popen_kw: Dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "env": env,
        }
        self.proc = subprocess.Popen(self.cmd, **popen_kw)
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



def evaluate_policy(ctx: RuneContext, step: Step) -> PolicyDecision:
    spell_risk = str(ctx.spell.constraints.get("risk", "low"))
    decision = PolicyDecision()
    for rule in ctx.policy.get("deny", []):
        if rule_matches(step, spell_risk, rule):
            decision.allowed = False
            decision.approved = False
            decision.reasons.append(str(rule.get("reason", "Denied by policy.")))
    if ctx.simulate and step.effect == "write":
        decision.simulated = True
        decision.requires_approval = False
        decision.approved = True
        decision.reasons.append("Simulate mode suppresses external write side effects.")
        return decision
    if step.effect in set(ctx.spell.constraints.get("requires_approval_for", [])):
        decision.requires_approval = True
        decision.reasons.append("Spell constraints require approval for this effect.")
    for rule in ctx.policy.get("requires_approval", []):
        if rule_matches(step, spell_risk, rule):
            decision.requires_approval = True
            decision.reasons.append(str(rule.get("reason", "Approval required by policy.")))
    decision.approved = (step.step_id in ctx.approvals or "all" in ctx.approvals) if decision.requires_approval else True
    return decision



def apply_output_schema(step: Step, value: Any, spell: Spell) -> None:
    if step.output_schema is not None:
        jsonschema.validate(instance=value, schema=load_schema(step.output_schema, spell.source_path.parent))



def run_step(
    ctx: RuneContext,
    step: Step,
    decision: PolicyDecision,
    compensation_for: Optional[str] = None,
) -> RuneOutcome:
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
    ctx.write_json(ctx.artifact_dir / f"{ctx.spell.name}.trace.json", trace)
    ctx.write_json(ctx.artifact_dir / f"{ctx.spell.name}.prov.json", build_prov_document(ctx, plan))
    ctx.write_text(ctx.artifact_dir / f"{ctx.spell.name}.scxml", build_scxml(ctx.spell, plan))
    ctx.write_json(ctx.artifact_dir / f"{ctx.spell.name}.proofs.json", build_proof_summary(ctx.proofs))



def execute_spell(
    spell: Spell,
    capabilities: Sequence[str],
    approvals: Set[str],
    simulate: bool,
    policy_path: Path,
    artifact_dir: Path,
) -> Dict[str, Any]:
    check_spell_capabilities(spell, capabilities)
    ctx = RuneContext(spell, capabilities, approvals, simulate, artifact_dir, load_json(policy_path))
    plan = compile_plan(spell)
    rollback_map = {step.compensates: step for step in spell.rollback if step.compensates}
    started_at = utc_now()
    status = "succeeded"
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
        "proofs": build_proof_summary(ctx.proofs),
    }
    if spell.witness.get("record", True):
        export_witnesses(ctx, plan, trace)
    final_step_id = plan[-1].step_id if status == "succeeded" else None
    final_meta = ctx.step_meta.get(final_step_id or "", {})
    final_value = ctx.values.get(final_step_id) if final_step_id else None
    result = {
        "spell": spell.name,
        "intent": spell.intent,
        "status": status,
        "error": error_message,
        "final_step": final_step_id,
        "final": final_value,
        "final_confidence": final_meta.get("confidence"),
        "final_entropy": final_meta.get("entropy"),
        "trace_path": str(ctx.artifact_dir / f"{spell.name}.trace.json"),
        "prov_path": str(ctx.artifact_dir / f"{spell.name}.prov.json"),
        "scxml_path": str(ctx.artifact_dir / f"{spell.name}.scxml"),
        "proof_path": str(ctx.artifact_dir / f"{spell.name}.proofs.json"),
        "proofs": build_proof_summary(ctx.proofs),
        "execution_id": ctx.execution_id,
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
    return RuneOutcome({"archived": count, "status": "simulated_archive" if ctx.simulate else "archive_complete"}, 0.98, side_effect=not ctx.simulate)


@REGISTRY.register("gate.emit", capability="write")
def rune_emit(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    artifact = ctx.resolve(args.get("from"))
    target = str(ctx.resolve(args.get("target", "stdout")))
    status = "simulated_write" if ctx.simulate else "emitted"
    return RuneOutcome({"target": target, "emitted": artifact, "status": status}, 0.98, side_effect=not ctx.simulate)


@REGISTRY.register("gate.file_write", capability="write")
def rune_file_write(ctx: RuneContext, step: Step, args: Dict[str, Any]) -> RuneOutcome:
    artifact = ctx.resolve(args.get("from"))
    raw_path = ctx.resolve(args.get("path"))
    if raw_path is None:
        raise StepExecutionError("gate.file_write requires 'path'")
    target = ctx.maybe_path(str(raw_path))
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
    client = MCPClient(cmd)
    ctx.add_mcp_client(client)
    return RuneOutcome(client.call_tool(name, arguments), 0.97, "Tool behavior depends on the remote MCP server implementation.", True)



def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an Axiomurgy spell or spellbook entrypoint.")
    parser.add_argument("target", help="Path to a .spell.json file, a spellbook directory, or spellbook.json")
    parser.add_argument("--entrypoint", default=None, help="Spellbook entrypoint name")
    parser.add_argument("--approve", action="append", default=[])
    parser.add_argument("--policy", default=None)
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--capability", action="append", default=[])
    return parser.parse_args(argv)



def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"ERROR: File not found: {target}")
        return 2
    try:
        resolved = resolve_run_target(
            target,
            args.entrypoint,
            Path(args.policy).resolve() if args.policy else None,
            Path(args.artifact_dir).resolve() if args.artifact_dir else None,
        )
        capabilities = {"read", "memory", "reason", "transform", "verify", "approve", "simulate", "write"}
        capabilities.update(args.capability)
        result = execute_spell(
            resolved.spell,
            sorted(capabilities),
            set(args.approve),
            bool(args.simulate),
            resolved.policy_path,
            resolved.artifact_dir,
        )
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
