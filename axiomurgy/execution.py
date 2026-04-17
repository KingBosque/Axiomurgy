"""Execution loop, step runtime, RuneContext, and witness export helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

import jsonschema

from .fingerprint import compute_spell_fingerprints
from .legacy import (
    CAPABILITY_KINDS,
    ApprovalRequiredError,
    CapabilityDeniedError,
    CapabilityError,
    CompensationEvent,
    PolicyDecision,
    PolicyDeniedError,
    ProofFailure,
    RuneOutcome,
    Spell,
    Step,
    TraceEvent,
    VERSION,
)
from .planning import (
    capability_kinds_for_step,
    check_spell_capabilities,
    compile_plan,
    evaluate_policy_static,
    resolve_static_value,
    summarize_write_target,
)
from .proof import build_proof_summary, extract_proofs, normalize_proof
from .runes import MCPClient, REGISTRY
from .util import (
    ROOT,
    canonical_json,
    extract_references,
    load_json,
    load_schema,
    normalize_paths_for_portability,
    utc_now,
)


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

    def add_mcp_client(self, client: MCPClient) -> None:
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


def evaluate_policy(ctx: RuneContext, step: Step) -> PolicyDecision:
    ctx.record_capability_event(
        kind="policy.evaluate",
        step_id=step.step_id,
        rune=step.rune,
        target={"effect": step.effect},
    )
    return evaluate_policy_static(ctx.spell, ctx.policy, ctx.approvals, ctx.simulate, step)


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
    lines += ['  <final id="done"/>', "</scxml>"]
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


__all__ = [
    "RuneContext",
    "apply_output_schema",
    "run_step",
    "build_prov_document",
    "normalize_trace_for_diff",
    "normalize_prov_for_diff",
    "normalize_proofs_for_diff",
    "build_scxml",
    "export_witnesses",
    "execute_spell",
    "evaluate_policy",
]
