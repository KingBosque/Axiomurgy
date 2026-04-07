#!/usr/bin/env python3
"""Axiomurgy v0.2 reference runtime.

This runtime treats every spell as a typed, policy-gated, provenance-bearing workflow.

v0.2 adds:
- Draft 2020-12 JSON Schema validation for spells
- dependency-aware execution planning (requires/depends_on)
- policy evaluation and human approval gates
- rollback / compensation semantics for side effects
- provenance export and raw execution traces
- SCXML plan export
- MCP stdio integration (resources + tools)
- OpenAPI-driven HTTP calls
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlencode, urljoin, urlparse

from jsonschema import Draft202012Validator
import requests
import yaml


VERSION = "0.2.0"
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class AxiomurgyError(Exception):
    """Base error."""


class SpellValidationError(AxiomurgyError):
    pass


class CapabilityError(AxiomurgyError):
    pass


@dataclass
class Compensation:
    rune: str
    args: Dict[str, Any] = field(default_factory=dict)
    effect: str = "write"


@dataclass
class Step:
    step_id: str
    rune: str
    args: Dict[str, Any] = field(default_factory=dict)
    requires: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    effect: str = "transform"
    on_failure: str = "rollback"
    compensate: Optional[Compensation] = None


@dataclass
class Spell:
    name: str
    intent: str
    inputs: Dict[str, Any]
    constraints: Dict[str, Any]
    graph: List[Step]
    witness: Dict[str, Any]
    source_path: Path


@dataclass
class TraceEvent:
    t: str
    kind: str
    step_id: str
    rune: str
    effect: str
    args: Dict[str, Any]
    output: Any = None
    error: Optional[str] = None


@dataclass
class PolicyDecision:
    allowed: bool = True
    requires_approval: bool = False
    approved: bool = False
    reason: str = ""


class RuneContext:
    def __init__(
        self,
        spell: Spell,
        capabilities: Optional[List[str]] = None,
        approvals: Optional[Set[str]] = None,
        approve_all: bool = False,
        trace: Optional[List[TraceEvent]] = None,
    ) -> None:
        self.spell = spell
        self.capabilities = set(capabilities or [])
        self.approvals = approvals or set()
        self.approve_all = approve_all
        self.values: Dict[str, Any] = {"inputs": spell.inputs}
        self.trace: List[TraceEvent] = trace if trace is not None else []

    def resolve(self, value: Any) -> Any:
        """Resolve $step references and dotted $input references recursively."""
        if isinstance(value, str):
            if value.startswith("$"):
                key = value[1:]
                parts = key.split(".")
                if not parts:
                    raise KeyError(f"Unknown reference: {value}")
                if parts[0] not in self.values:
                    raise KeyError(f"Unknown reference: {value}")
                current: Any = self.values[parts[0]]
                for part in parts[1:]:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        raise KeyError(f"Unknown reference: {value}")
                return current
            return value
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        return value

    def add_trace(self, event: TraceEvent) -> None:
        self.trace.append(event)


RuneHandler = Callable[[RuneContext, Dict[str, Any]], Any]


class RuneRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, RuneHandler] = {}
        self._capability_map: Dict[str, str] = {}

    def register(self, name: str, capability: str) -> Callable[[RuneHandler], RuneHandler]:
        def decorator(func: RuneHandler) -> RuneHandler:
            self._handlers[name] = func
            self._capability_map[name] = capability
            return func
        return decorator

    def handler_for(self, name: str) -> RuneHandler:
        if name not in self._handlers:
            raise KeyError(f"Unknown rune: {name}")
        return self._handlers[name]

    def required_capability(self, name: str) -> str:
        return self._capability_map[name]


REGISTRY = RuneRegistry()

_SPELL_SCHEMA: Optional[Dict[str, Any]] = None


def _spell_schema() -> Dict[str, Any]:
    global _SPELL_SCHEMA
    if _SPELL_SCHEMA is None:
        path = Path(__file__).resolve().parent / "spell.schema.json"
        _SPELL_SCHEMA = json.loads(path.read_text(encoding="utf-8"))
    return _SPELL_SCHEMA


def _validate_spell_against_schema(doc: Dict[str, Any]) -> None:
    validator = Draft202012Validator(_spell_schema())
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    parts: List[str] = []
    for err in errors[:8]:
        path = "/".join(str(p) for p in err.absolute_path) or "(root)"
        parts.append(f"{path}: {err.message}")
    raise SpellValidationError("; ".join(parts))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_textish(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve_path(base: Path, maybe_path: str) -> Path:
    path = Path(maybe_path)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _bool(v: Any) -> bool:
    return bool(v)


def _risk_of(spell: Spell) -> str:
    risk = str(spell.constraints.get("risk", "medium"))
    return risk if risk in RISK_ORDER else "medium"


def _policy_path_for(spell: Spell) -> Optional[Path]:
    raw = spell.constraints.get("policy")
    if not raw:
        default = spell.source_path.parent / "policies" / "default.policy.json"
        return default if default.exists() else None
    return _resolve_path(spell.source_path.parent, str(raw))


def _load_policy(spell: Spell) -> Dict[str, Any]:
    path = _policy_path_for(spell)
    if path is None:
        return {"requires_approval": [], "deny": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_any(value: str, allowed: Sequence[str]) -> bool:
    return value in set(allowed)


def _policy_decide(policy: Dict[str, Any], spell: Spell, step: Step) -> PolicyDecision:
    decision = PolicyDecision()
    # deny
    for rule in policy.get("deny", []) or []:
        runes = rule.get("rune") or []
        if runes and _matches_any(step.rune, runes):
            decision.allowed = False
            decision.reason = str(rule.get("reason", "Denied by policy"))
            return decision

    # requires approval
    for rule in policy.get("requires_approval", []) or []:
        runes = rule.get("rune") or []
        effects = rule.get("effect") or []
        min_risk = rule.get("min_risk")
        if runes and _matches_any(step.rune, runes):
            decision.requires_approval = True
            decision.reason = str(rule.get("reason", "Approval required by policy"))
        if effects and _matches_any(step.effect, effects):
            if min_risk:
                if RISK_ORDER[_risk_of(spell)] >= RISK_ORDER.get(str(min_risk), 0):
                    decision.requires_approval = True
                    decision.reason = str(rule.get("reason", "Approval required by policy"))
            else:
                decision.requires_approval = True
                decision.reason = str(rule.get("reason", "Approval required by policy"))
    return decision


def _approval_required(spell: Spell, step: Step, decision: PolicyDecision) -> bool:
    constraints = spell.constraints or {}
    requires = set(constraints.get("requires_approval_for", []) or [])
    if step.effect in requires:
        return True
    return decision.requires_approval


def _ensure_approved(ctx: RuneContext, step: Step, decision: PolicyDecision) -> None:
    if not _approval_required(ctx.spell, step, decision):
        return
    if ctx.approve_all or step.step_id in ctx.approvals:
        decision.approved = True
        return
    raise AxiomurgyError(
        f"Approval required for step '{step.step_id}' ({step.rune}). "
        f"Run with --approve {step.step_id} (or --approve all)."
    )


def _steps_from_json(raw_steps: List[Any], section: str) -> List[Step]:
    steps: List[Step] = []
    seen_ids: Set[str] = set()
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise SpellValidationError(f"{section}: each step must be an object")
        step_id = str(raw_step["id"])
        if step_id in seen_ids:
            raise SpellValidationError(f"{section}: duplicate step id: {step_id}")
        seen_ids.add(step_id)

        compensate_raw = raw_step.get("compensate")
        compensate = None
        if compensate_raw is not None:
            compensate = Compensation(
                rune=str(compensate_raw["rune"]),
                args=compensate_raw.get("args", {}) or {},
                effect=str(compensate_raw.get("effect", "write")),
            )

        steps.append(
            Step(
                step_id=step_id,
                rune=str(raw_step["rune"]),
                args=raw_step.get("args", {}) or {},
                requires=list(raw_step.get("requires", []) or []),
                depends_on=list(raw_step.get("depends_on", []) or []),
                effect=str(raw_step.get("effect", "transform")),
                on_failure=str(raw_step.get("on_failure", "rollback")),
                compensate=compensate,
            )
        )
    return steps


@REGISTRY.register("mirror.read", capability="read")
def rune_mirror_read(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    source = args.get("input")
    if source is None:
        raise ValueError("mirror.read requires an 'input' argument")
    source = ctx.resolve(source)

    def read_one(item: Any) -> str:
        if isinstance(item, str):
            if item.startswith("file://"):
                path = Path(item[7:])
                if not path.is_absolute():
                    path = (ctx.spell.source_path.parent / path).resolve()
                return _read_textish(path)
            # convenience: treat plain paths as files if they exist relative to spell
            p = (ctx.spell.source_path.parent / item).resolve()
            if p.exists() and p.is_file():
                return _read_textish(p)
        return str(item)

    if isinstance(source, list):
        return [read_one(item) for item in source]
    return read_one(source)


@REGISTRY.register("archive.retrieve", capability="memory")
def rune_archive_retrieve(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    key = args.get("key", "")
    key = str(ctx.resolve(key))
    return {
        "memory_key": key,
        "note": f"No external memory backend attached for '{key}'. Returning placeholder memory.",
    }


@REGISTRY.register("lantern.classify", capability="reason")
def rune_lantern_classify(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    items = ctx.resolve(args.get("items", []))
    if not isinstance(items, list):
        items = [items]
    labels = []
    for item in items:
        text = str(item).lower()
        if any(word in text for word in ["urgent", "asap", "immediately", "today"]):
            label = "urgent"
        elif any(word in text for word in ["invoice", "receipt", "payment"]):
            label = "finance"
        else:
            label = "normal"
        labels.append({"text": item, "label": label})
    return labels


@REGISTRY.register("forge.summarize", capability="transform")
def rune_forge_summarize(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    content = ctx.resolve(args.get("from", ""))
    if isinstance(content, list):
        texts = [str(x) for x in content]
    else:
        texts = [str(content)]

    summaries: List[str] = ["Recurring themes (heuristic): evidence, capability, provenance, and gated writes.\n"]
    for idx, text in enumerate(texts, start=1):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        head = lines[:3] if lines else [text[:160].strip() or "(empty)"]
        summaries.append(f"Source {idx}: " + " | ".join(head)[:400])
    return "\n".join(summaries)


@REGISTRY.register("forge.reply_drafts", capability="transform")
def rune_forge_reply_drafts(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    items = ctx.resolve(args.get("items", []))
    if not isinstance(items, list):
        items = [items]
    drafts = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("text", ""))
            label = str(item.get("label", "normal"))
        else:
            text = str(item)
            label = "normal"
        if label == "urgent":
            reply = "Acknowledged. I have flagged this as urgent and prepared it for immediate human review."
        elif label == "finance":
            reply = "Thanks. I have routed this to the finance queue and prepared a draft response for confirmation."
        else:
            reply = "Thanks for the message. I have prepared a brief reply draft for review."
        drafts.append({"original": text, "label": label, "draft": reply})
    return drafts


@REGISTRY.register("seal.review", capability="verify")
def rune_seal_review(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    artifact = ctx.resolve(args.get("from"))
    required_markers = args.get("must_include", [])
    text = json.dumps(artifact, ensure_ascii=False) if not isinstance(artifact, str) else artifact
    missing = [marker for marker in required_markers if marker not in text]
    return {
        "approved": not missing,
        "missing": missing,
        "artifact": artifact,
        "note": "Minimal review only. Replace with real policy and citation checks in production.",
    }


@REGISTRY.register("seal.require", capability="verify")
def rune_seal_require(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    value = ctx.resolve(args.get("value"))
    equals = ctx.resolve(args.get("equals"))
    message = str(ctx.resolve(args.get("message", "Requirement failed")))
    ok = value == equals
    if not ok:
        raise AxiomurgyError(message)
    return {"ok": True, "value": value, "equals": equals}


@REGISTRY.register("seal.approval_gate", capability="approve")
def rune_seal_approval_gate(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    reason = str(ctx.resolve(args.get("reason", "")))
    auto = bool(args.get("auto_approve", False))
    return {
        "approved": auto,
        "reason": reason,
        "status": "approved" if auto else "pending_human_review",
    }


@REGISTRY.register("veil.simulate", capability="simulate")
def rune_veil_simulate(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    artifact = ctx.resolve(args.get("from"))
    return {
        "simulated": True,
        "preview": artifact,
        "note": "Dry-run only; no external side effects were committed.",
    }


@REGISTRY.register("gate.archive", capability="write")
def rune_gate_archive(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    artifact = ctx.resolve(args.get("from"))
    count = len(artifact) if isinstance(artifact, list) else 1
    return {
        "archived": count,
        "status": "simulated_archive",
    }


@REGISTRY.register("gate.emit", capability="write")
def rune_gate_emit(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    artifact = ctx.resolve(args.get("from"))
    target = str(ctx.resolve(args.get("target", "stdout")))
    return {
        "target": target,
        "emitted": artifact,
        "status": "simulated_write",
    }


@REGISTRY.register("gate.file_write", capability="write")
def rune_gate_file_write(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    content = ctx.resolve(args.get("from", ""))
    path_raw = str(ctx.resolve(args.get("path", "")))
    if not path_raw:
        raise ValueError("gate.file_write requires 'path'")
    mode = str(ctx.resolve(args.get("mode", "text")))
    path = _resolve_path(ctx.spell.source_path.parent, path_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "json":
        path.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(content), encoding="utf-8")
    return {"wrote": True, "path": str(path), "bytes": path.stat().st_size}


@REGISTRY.register("gate.file_delete", capability="write")
def rune_gate_file_delete(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    path_raw = str(ctx.resolve(args.get("path", "")))
    if not path_raw:
        raise ValueError("gate.file_delete requires 'path'")
    path = _resolve_path(ctx.spell.source_path.parent, path_raw)
    if path.exists():
        path.unlink()
        return {"deleted": True, "path": str(path)}
    return {"deleted": False, "path": str(path)}


@REGISTRY.register("forge.template", capability="transform")
def rune_forge_template(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    template = str(ctx.resolve(args.get("template", "")))
    bindings = ctx.resolve(args.get("bindings", {})) or {}
    if not isinstance(bindings, dict):
        raise ValueError("forge.template bindings must be an object")
    resolved = {k: ctx.resolve(v) for k, v in bindings.items()}
    return template.format(**resolved)


class McpClient:
    def __init__(self, server_cmd: List[str], cwd: Path) -> None:
        self._proc = subprocess.Popen(
            server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            text=True,
        )
        self._next_id = 1

    def _call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise AxiomurgyError("MCP server closed stdout")
        resp = json.loads(line)
        if "error" in resp:
            raise AxiomurgyError(f"MCP error: {resp['error']}")
        return resp["result"]

    def initialize(self) -> None:
        self._call("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "axiomurgy"}})

    def resources_list(self) -> List[Dict[str, Any]]:
        return self._call("resources/list", {}).get("resources", [])

    def resources_read(self, uri: str) -> str:
        result = self._call("resources/read", {"uri": uri})
        contents = result.get("contents", [])
        if contents and isinstance(contents, list) and isinstance(contents[0], dict):
            return str(contents[0].get("text", ""))
        return ""

    def tools_call(self, name: str, arguments: Dict[str, Any]) -> Any:
        return self._call("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass


_MCP_CLIENTS: Dict[str, McpClient] = {}


def _mcp_client(server: str, base: Path) -> McpClient:
    key = str(_resolve_path(base, server))
    if key in _MCP_CLIENTS:
        return _MCP_CLIENTS[key]
    cmd = [sys.executable, key]
    client = McpClient(cmd, cwd=base)
    client.initialize()
    _MCP_CLIENTS[key] = client
    return client


@REGISTRY.register("mirror.mcp_resource", capability="read")
def rune_mirror_mcp_resource(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    server = str(ctx.resolve(args.get("server", "")))
    uri = ctx.resolve(args.get("uri"))
    if not server:
        raise ValueError("mirror.mcp_resource requires 'server'")
    client = _mcp_client(server, ctx.spell.source_path.parent)

    def read_one(u: Any) -> str:
        return client.resources_read(str(u))

    if isinstance(uri, list):
        return [read_one(u) for u in uri]
    return read_one(uri)


@REGISTRY.register("gate.mcp_call_tool", capability="write")
def rune_gate_mcp_call_tool(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    server = str(ctx.resolve(args.get("server", "")))
    name = str(ctx.resolve(args.get("name", "")))
    arguments = ctx.resolve(args.get("arguments", {})) or {}
    if not server or not name:
        raise ValueError("gate.mcp_call_tool requires 'server' and 'name'")
    client = _mcp_client(server, ctx.spell.source_path.parent)
    return client.tools_call(name, arguments)


def _openapi_operation(spec: Dict[str, Any], operation_id: str) -> Tuple[str, str]:
    paths = spec.get("paths", {}) or {}
    for path, methods in paths.items():
        for method, details in (methods or {}).items():
            if isinstance(details, dict) and details.get("operationId") == operation_id:
                return method.upper(), path
    raise AxiomurgyError(f"OpenAPI operationId not found: {operation_id}")


def _safe_base_url(spell: Spell, base_url: str) -> None:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost"}:
        return
    if _bool(spell.constraints.get("allow_remote_http", False)):
        return
    raise AxiomurgyError("Remote HTTP blocked. Set constraints.allow_remote_http=true to allow.")


@REGISTRY.register("gate.openapi_call", capability="write")
def rune_gate_openapi_call(ctx: RuneContext, args: Dict[str, Any]) -> Any:
    spec_path = str(ctx.resolve(args.get("spec", "")))
    operation_id = str(ctx.resolve(args.get("operationId", "")))
    call_args = ctx.resolve(args.get("arguments", {})) or {}
    if not spec_path or not operation_id:
        raise ValueError("gate.openapi_call requires 'spec' and 'operationId'")
    spec_file = _resolve_path(ctx.spell.source_path.parent, spec_path)
    spec = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
    base_url = str((spec.get("servers") or [{}])[0].get("url", "")).rstrip("/")
    if not base_url:
        raise AxiomurgyError("OpenAPI spec missing servers[0].url")
    _safe_base_url(ctx.spell, base_url)

    method, path_template = _openapi_operation(spec, operation_id)
    path_params = call_args.get("path", {}) or {}
    query_params = call_args.get("query", {}) or {}
    body = call_args.get("body")

    path = path_template
    for k, v in path_params.items():
        path = path.replace("{" + k + "}", str(v))
    url = base_url + path
    if query_params:
        url += "?" + urlencode(query_params, doseq=True)

    resp = requests.request(method, url, json=body, timeout=10)
    try:
        payload = resp.json()
    except Exception:
        payload = {"text": resp.text}
    return {"status_code": resp.status_code, "body": payload}


def validate_spell_document(doc: Dict[str, Any]) -> Spell:
    _validate_spell_against_schema(doc)
    graph = _steps_from_json(doc["graph"], "graph")
    return Spell(
        name=doc["spell"],
        intent=doc["intent"],
        inputs=doc.get("inputs", {}),
        constraints=doc.get("constraints", {}),
        graph=graph,
        witness=doc.get("witness", {"record": True, "format": "prov-like"}),
        source_path=Path("<unknown>"),
    )


def load_spell(path: Path) -> Spell:
    document = json.loads(path.read_text(encoding="utf-8"))
    spell = validate_spell_document(document)
    spell.source_path = path.resolve()
    return spell


def _compile_plan(steps: List[Step]) -> List[Step]:
    by_id = {s.step_id: s for s in steps}
    deps: Dict[str, Set[str]] = {}
    for s in steps:
        deps[s.step_id] = set(s.requires) | set(s.depends_on)
        for d in deps[s.step_id]:
            if d not in by_id:
                raise SpellValidationError(f"Unknown dependency '{d}' in step '{s.step_id}'")

    ready = [s.step_id for s in steps if not deps[s.step_id]]
    order: List[str] = []
    while ready:
        step_id = ready.pop(0)
        order.append(step_id)
        for other in steps:
            if step_id in deps[other.step_id]:
                deps[other.step_id].remove(step_id)
                if not deps[other.step_id] and other.step_id not in order and other.step_id not in ready:
                    ready.append(other.step_id)

    if len(order) != len(steps):
        stuck = [sid for sid, ds in deps.items() if ds]
        raise SpellValidationError(f"Dependency cycle or unresolved deps involving: {stuck}")
    return [by_id[sid] for sid in order]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_scxml(path: Path, spell: Spell, plan: List[Step]) -> None:
    # Minimal SCXML: linearized plan transitions.
    states = "\n".join(
        [
            f'  <state id="{s.step_id}"><transition event="done" target="{plan[i+1].step_id if i+1 < len(plan) else "end"}"/></state>'
            for i, s in enumerate(plan)
        ]
    )
    scxml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<scxml xmlns="http://www.w3.org/2005/07/scxml" version="1.0" initial="{plan[0].step_id}">\n'
        f"{states}\n"
        '  <final id="end"/>\n'
        "</scxml>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scxml, encoding="utf-8")


def execute_spell(
    spell: Spell,
    capabilities: Optional[List[str]] = None,
    approvals: Optional[Set[str]] = None,
    approve_all: bool = False,
) -> Dict[str, Any]:
    ctx = RuneContext(spell, capabilities=capabilities, approvals=approvals, approve_all=approve_all)
    required_capabilities = set(spell.constraints.get("required_capabilities", []))
    if not required_capabilities.issubset(ctx.capabilities):
        missing = sorted(required_capabilities - ctx.capabilities)
        raise CapabilityError(f"Missing spell-level capabilities: {missing}")

    plan = _compile_plan(spell.graph)
    policy = _load_policy(spell)
    simulate_only = _bool(spell.constraints.get("simulate_only", False))

    compensation_stack: List[Tuple[Step, Compensation]] = []
    try:
        for step in plan:
            decision = _policy_decide(policy, spell, step)
            if not decision.allowed:
                raise AxiomurgyError(f"Policy denied step '{step.step_id}': {decision.reason}")
            _ensure_approved(ctx, step, decision)

            rune_cap = REGISTRY.required_capability(step.rune)
            if rune_cap not in ctx.capabilities:
                raise CapabilityError(
                    f"Step '{step.step_id}' requires capability '{rune_cap}' for rune '{step.rune}'"
                )

            handler = REGISTRY.handler_for(step.rune)
            resolved_args = ctx.resolve(step.args)
            ctx.add_trace(
                TraceEvent(
                    t=_now(),
                    kind="step_start",
                    step_id=step.step_id,
                    rune=step.rune,
                    effect=step.effect,
                    args=resolved_args,
                )
            )

            if simulate_only and step.effect == "write":
                output = {"simulated": True, "note": "simulate_only enabled", "args": resolved_args}
            else:
                output = handler(ctx, resolved_args)
            ctx.values[step.step_id] = output
            ctx.add_trace(
                TraceEvent(
                    t=_now(),
                    kind="step_end",
                    step_id=step.step_id,
                    rune=step.rune,
                    effect=step.effect,
                    args=resolved_args,
                    output=output,
                )
            )
            if step.compensate is not None and step.effect == "write":
                compensation_stack.append((step, step.compensate))
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        ctx.add_trace(
            TraceEvent(
                t=_now(),
                kind="spell_error",
                step_id="",
                rune="",
                effect="",
                args={},
                error=err,
            )
        )
        # rollback: run compensations reverse order
        for original_step, comp in reversed(compensation_stack):
            try:
                handler = REGISTRY.handler_for(comp.rune)
                resolved_args = ctx.resolve(comp.args)
                ctx.add_trace(
                    TraceEvent(
                        t=_now(),
                        kind="compensate_start",
                        step_id=original_step.step_id,
                        rune=comp.rune,
                        effect=comp.effect,
                        args=resolved_args,
                    )
                )
                if simulate_only and comp.effect == "write":
                    out = {"simulated": True, "note": "simulate_only enabled", "args": resolved_args}
                else:
                    out = handler(ctx, resolved_args)
                ctx.add_trace(
                    TraceEvent(
                        t=_now(),
                        kind="compensate_end",
                        step_id=original_step.step_id,
                        rune=comp.rune,
                        effect=comp.effect,
                        args=resolved_args,
                        output=out,
                    )
                )
            except Exception as rollback_exc:
                ctx.add_trace(
                    TraceEvent(
                        t=_now(),
                        kind="compensate_error",
                        step_id=original_step.step_id,
                        rune=comp.rune,
                        effect=comp.effect,
                        args={},
                        error=f"{type(rollback_exc).__name__}: {rollback_exc}",
                    )
                )
        raise
    finally:
        witness = spell.witness or {}
        if witness.get("record", True):
            base = spell.source_path.parent
            prov_path = _resolve_path(base, witness.get("path", f"artifacts/{spell.name}.prov.json"))
            trace_path = _resolve_path(base, witness.get("trace_path", f"artifacts/{spell.name}.trace.json"))
            scxml_path = _resolve_path(base, witness.get("scxml_path", f"artifacts/{spell.name}.scxml"))

            include_inputs = witness.get("include_inputs", True)
            prov = {
                "version": VERSION,
                "spell": {"name": spell.name, "intent": spell.intent, "source": str(spell.source_path)},
                "constraints": spell.constraints,
                "inputs": spell.inputs if include_inputs else {"redacted": True},
                "plan": [asdict(s) for s in plan],
                "values": ctx.values,
            }
            _write_json(prov_path, prov)
            _write_json(trace_path, [asdict(e) for e in ctx.trace])
            if plan:
                _write_scxml(scxml_path, spell, plan)

    final_step_id = plan[-1].step_id if plan else ""
    return {
        "version": VERSION,
        "spell": spell.name,
        "intent": spell.intent,
        "final": ctx.values.get(final_step_id),
        "values": ctx.values,
        "trace_events": len(ctx.trace),
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="axiomurgy")
    parser.add_argument("spell_json", help="Path to spell JSON")
    parser.add_argument("--approve", action="append", default=[], help="Approve a step id, or 'all'")
    args = parser.parse_args(argv[1:])

    path = Path(args.spell_json)
    if not path.exists():
        print(f"File not found: {path}")
        return 2

    try:
        spell = load_spell(path)
        capabilities = ["read", "memory", "reason", "transform", "verify", "approve", "simulate", "write"]
        approvals = set(a for a in args.approve if a != "all")
        approve_all = "all" in set(args.approve)
        result = execute_spell(spell, capabilities=capabilities, approvals=approvals, approve_all=approve_all)
    except (AxiomurgyError, CapabilityError, KeyError, ValueError, json.JSONDecodeError, requests.RequestException) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
