#!/usr/bin/env python3
"""Minimal reference runtime for Axiomurgy v0.1.

This is intentionally small and offline. It demonstrates the core ideas:
- spells as structured data
- explicit rune registry
- capability checks
- provenance logging
- separation between read, transform, verify, and write effects

Usage:
    python axiomurgy.py examples/research_brief.spell.json
    python axiomurgy.py examples/inbox_triage.spell.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from jsonschema import Draft202012Validator


class SpellValidationError(Exception):
    pass


class CapabilityError(Exception):
    pass


@dataclass
class Step:
    step_id: str
    rune: str
    args: Dict[str, Any] = field(default_factory=dict)
    requires: List[str] = field(default_factory=list)
    produces: Optional[str] = None
    effect: str = "transform"


@dataclass
class Spell:
    name: str
    intent: str
    inputs: Dict[str, Any]
    constraints: Dict[str, Any]
    graph: List[Step]
    witness: Dict[str, Any]
    rollback: List[Step] = field(default_factory=list)


@dataclass
class ProvenanceRecord:
    step_id: str
    rune: str
    effect: str
    args: Dict[str, Any]
    output_preview: str


class RuneContext:
    def __init__(self, spell: Spell, capabilities: Optional[List[str]] = None) -> None:
        self.spell = spell
        self.capabilities = set(capabilities or [])
        self.values: Dict[str, Any] = {"inputs": spell.inputs}
        self.provenance: List[ProvenanceRecord] = []

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

    def add_record(self, step: Step, output: Any) -> None:
        preview = repr(output)
        if len(preview) > 240:
            preview = preview[:237] + "..."
        self.provenance.append(
            ProvenanceRecord(
                step_id=step.step_id,
                rune=step.rune,
                effect=step.effect,
                args=step.args,
                output_preview=preview,
            )
        )


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


def _steps_from_json(raw_steps: List[Any], section: str) -> List[Step]:
    steps: List[Step] = []
    seen_ids: set[str] = set()
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise SpellValidationError(f"{section}: each step must be an object")
        step_id = raw_step["id"]
        if step_id in seen_ids:
            raise SpellValidationError(f"{section}: duplicate step id: {step_id}")
        seen_ids.add(step_id)
        produces = raw_step.get("produces")
        if produces is not None and not isinstance(produces, str):
            raise SpellValidationError(f"{section}: step {step_id!r}: 'produces' must be a string")
        steps.append(
            Step(
                step_id=step_id,
                rune=raw_step["rune"],
                args=raw_step.get("args", {}),
                requires=raw_step.get("requires", []),
                produces=produces,
                effect=raw_step.get("effect", "transform"),
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
        if isinstance(item, str) and item.startswith("file://"):
            path = Path(item[7:])
            return path.read_text(encoding="utf-8")
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

    summaries: List[str] = []
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


def validate_spell_document(doc: Dict[str, Any]) -> Spell:
    _validate_spell_against_schema(doc)
    graph = _steps_from_json(doc["graph"], "graph")
    rollback_raw = doc.get("rollback") or []
    rollback = _steps_from_json(rollback_raw, "rollback")
    return Spell(
        name=doc["spell"],
        intent=doc["intent"],
        inputs=doc.get("inputs", {}),
        constraints=doc.get("constraints", {}),
        graph=graph,
        witness=doc.get("witness", {"record": True, "format": "prov-like"}),
        rollback=rollback,
    )


def execute_spell(spell: Spell, capabilities: Optional[List[str]] = None) -> Dict[str, Any]:
    ctx = RuneContext(spell, capabilities=capabilities)

    required_capabilities = set(spell.constraints.get("required_capabilities", []))
    if not required_capabilities.issubset(ctx.capabilities):
        missing = sorted(required_capabilities - ctx.capabilities)
        raise CapabilityError(f"Missing spell-level capabilities: {missing}")

    for step in spell.graph:
        rune_cap = REGISTRY.required_capability(step.rune)
        if rune_cap not in ctx.capabilities:
            raise CapabilityError(
                f"Step '{step.step_id}' requires capability '{rune_cap}' for rune '{step.rune}'"
            )
        handler = REGISTRY.handler_for(step.rune)
        resolved_args = ctx.resolve(step.args)
        output = handler(ctx, resolved_args)
        ctx.values[step.step_id] = output
        if spell.witness.get("record", True):
            ctx.add_record(step, output)

    final_step_id = spell.graph[-1].step_id
    return {
        "spell": spell.name,
        "intent": spell.intent,
        "final": ctx.values[final_step_id],
        "values": ctx.values,
        "provenance": [record.__dict__ for record in ctx.provenance],
    }


def load_spell(path: Path) -> Spell:
    document = json.loads(path.read_text(encoding="utf-8"))
    return validate_spell_document(document)


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("Usage: python axiomurgy.py <spell.json>")
        return 2

    path = Path(argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        return 2

    try:
        spell = load_spell(path)
        # Broad default capability set for the reference runtime.
        capabilities = ["read", "memory", "reason", "transform", "verify", "approve", "simulate", "write"]
        result = execute_spell(spell, capabilities=capabilities)
    except (SpellValidationError, CapabilityError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
