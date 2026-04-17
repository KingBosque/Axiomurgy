"""Translate Axiomurgy spells into Vermyth SemanticProgram-shaped JSON (parallel representation only)."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Sequence

from .legacy import Spell, Step
from .planning import compile_plan
from .runes import REGISTRY

VERMYTH_PROGRAM_EXPORT_VERSION = "0.1.0"

# Vermyth AspectID canonical names (CastNode with node_type CAST requires aspects).
_ASPECT_CYCLE = ("VOID", "FORM", "MOTION", "MIND", "DECAY", "LIGHT")

_FIXED_ISO = "2000-01-01T00:00:00+00:00"


def _stable_program_id(spell: Spell, ordered: Sequence[Step]) -> str:
    h = hashlib.sha256()
    h.update(spell.name.encode("utf-8"))
    h.update(b"\n")
    for s in ordered:
        h.update(s.step_id.encode("utf-8"))
        h.update(b"\n")
    return f"axiomurgy-{h.hexdigest()[:40]}"


def _effect_type(spell_effect: str) -> str:
    e = (spell_effect or "transform").lower()
    if e == "write":
        return "WRITE"
    if e == "read":
        return "READ"
    if e in ("network", "exec", "observe"):
        return e.upper()
    return "COMPUTE"


def _intent_payload(spell: Spell, step: Step) -> Dict[str, Any]:
    risk = str(spell.constraints.get("risk", "low"))
    if step.effect == "write":
        rev = "PARTIAL" if risk in ("low", "medium") else "IRREVERSIBLE"
    else:
        rev = "REVERSIBLE"
    tol = "HIGH" if risk in ("high", "critical") else "MEDIUM"
    objective = (spell.intent or spell.name)[:500]
    scope = f"axiomurgy:{step.step_id}:{step.rune}"[:200]
    return {
        "objective": objective,
        "scope": scope,
        "reversibility": rev,
        "side_effect_tolerance": tol,
    }


def spell_level_vermyth_intent(spell: Spell) -> Dict[str, str]:
    """
    Spell-level intent fields for Vermyth HTTP probes (/arcane/recommend, vermyth_gate).

    Uses the same risk / write-or-not rules as per-step _intent_payload, but a single
    objective line derived from name + intent + risk (for recommendation matching) and
    scope ``axiomurgy:{spell.name}``.
    """
    risk = str(spell.constraints.get("risk", "low"))
    tol = "HIGH" if risk in ("high", "critical") else "MEDIUM"
    plan = compile_plan(spell)
    has_write = any(s.effect == "write" for s in plan)
    if has_write:
        rev = "PARTIAL" if risk in ("low", "medium") else "IRREVERSIBLE"
    else:
        rev = "REVERSIBLE"
    summary_bits = [
        spell.name,
        str(spell.intent or ""),
        str(spell.constraints.get("risk", "low")),
    ]
    input_text = "\n".join(summary_bits)[:8000]
    objective = input_text[:500]
    scope = f"axiomurgy:{spell.name}"[:200]
    return {
        "objective": objective,
        "scope": scope,
        "reversibility": rev,
        "side_effect_tolerance": tol,
    }


def build_semantic_program(spell: Spell, *, plan: Sequence[Step] | None = None) -> Dict[str, Any]:
    """Return a Vermyth-compatible SemanticProgram JSON dict (subset used by compile_program)."""
    ordered = list(plan) if plan is not None else compile_plan(spell)
    if not ordered:
        raise ValueError("spell has no steps")
    unsupported: List[str] = []
    handlers = getattr(REGISTRY, "_handlers", {})
    for step in ordered:
        if step.rune not in handlers:
            unsupported.append(step.rune)
    nodes: List[Dict[str, Any]] = []
    for i, step in enumerate(ordered):
        aspect = _ASPECT_CYCLE[i % len(_ASPECT_CYCLE)]
        succ = [ordered[i + 1].step_id] if i + 1 < len(ordered) else []
        nodes.append(
            {
                "node_id": step.step_id,
                "node_type": "CAST",
                "aspects": [aspect],
                "intent": _intent_payload(spell, step),
                "successors": succ,
                "effects": [
                    {
                        "effect_type": _effect_type(step.effect),
                        "target": None,
                        "reversible": step.effect != "write",
                        "cost_hint": 0.0,
                    }
                ],
            }
        )
    program: Dict[str, Any] = {
        "program_id": _stable_program_id(spell, ordered),
        "name": spell.name[:200],
        "status": "DRAFT",
        "nodes": nodes,
        "entry_node_ids": [ordered[0].step_id],
        "metadata": {
            "axiomurgy_source_path": str(spell.source_path),
            "axiomurgy_export": VERMYTH_PROGRAM_EXPORT_VERSION,
            "unsupported_runes": unsupported,
        },
        "created_at": _FIXED_ISO,
        "updated_at": _FIXED_ISO,
    }
    return program


def build_vermyth_program_export(spell: Spell, *, plan: Sequence[Step] | None = None) -> Dict[str, Any]:
    """Wrapper document including format version (for CLI and witnesses)."""
    program = build_semantic_program(spell, plan=plan)
    return {
        "vermyth_program_export_version": VERMYTH_PROGRAM_EXPORT_VERSION,
        "program": program,
    }
