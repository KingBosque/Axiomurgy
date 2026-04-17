"""Optional Vermyth enrichment (recommendations, compile preview, gate, receipts) — additive only."""

from __future__ import annotations

import hashlib
import os
from enum import Enum
from typing import Any, Dict, List, Optional

import requests

from .adapters.vermyth_http import VermythHttpClient, VermythHttpError
from .legacy import PolicyDecision, ResolvedRunTarget, Spell, SpellValidationError
from .planning import compile_plan
from .vermyth_export import build_semantic_program, build_vermyth_program_export, spell_level_vermyth_intent


def _env_base_url() -> Optional[str]:
    v = os.environ.get("AXIOMURGY_VERMYTH_BASE_URL") or os.environ.get("VERMYTH_BASE_URL")
    return v.strip() if isinstance(v, str) and v.strip() else None


def _timeout_s() -> float:
    raw = os.environ.get("AXIOMURGY_VERMYTH_TIMEOUT_MS", "5000")
    try:
        return max(0.1, float(raw) / 1000.0)
    except ValueError:
        return 5.0


def _client() -> Optional[VermythHttpClient]:
    base = _env_base_url()
    if not base:
        return None
    return VermythHttpClient(base, timeout_s=_timeout_s())


def _hash_hint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _brief_semantic_recommendations(rec: Dict[str, Any]) -> None:
    """Add human-facing summary/rows (and advisory_note on failure) in place."""
    st = rec.get("status")
    if st != "ok":
        reason = rec.get("reason") or st
        rec["advisory_note"] = f"Semantic recommendations unavailable ({reason})."
        rec["summary"] = None
        rec["rows"] = []
        return
    items = rec.get("items") if isinstance(rec.get("items"), list) else []
    rows: List[Dict[str, Any]] = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        row: Dict[str, Any] = {
            "bundle_id": it.get("bundle_id"),
            "match_kind": it.get("match_kind"),
            "strength": it.get("strength"),
        }
        gu = it.get("guided_upgrade") if isinstance(it.get("guided_upgrade"), dict) else {}
        ins = gu.get("inspect") if isinstance(gu.get("inspect"), dict) else {}
        p = ins.get("http_get_path")
        if isinstance(p, str) and p.strip():
            row["inspect_hint"] = p.strip()
        rows.append(row)
    lat = rec.get("latency_ms")
    if rows:
        t0 = rows[0]
        rec["summary"] = (
            f"top={t0.get('bundle_id')} kind={t0.get('match_kind')} strength={t0.get('strength')} "
            f"latency_ms={lat}"
        )
    else:
        rec["summary"] = "no recommendations (empty list)"
    rec["rows"] = rows
    rec["advisory_note"] = None


def _recommend_input_payload(spell: Spell) -> tuple[str, Dict[str, Any]]:
    """Plain-text fingerprint line and task-shaped Vermyth `input` dict for /arcane/recommend."""
    summary_bits = [
        spell.name,
        str(spell.intent or ""),
        str(spell.constraints.get("risk", "low")),
    ]
    input_text = "\n".join(summary_bits)[:8000]
    payload: Dict[str, Any] = {"intent": spell_level_vermyth_intent(spell)}
    return input_text, payload


def fetch_semantic_recommendations(resolved: ResolvedRunTarget, *, skill_id: str = "decide") -> Dict[str, Any]:
    """Advisory-only bundle recommendations; never affects planning."""
    spell = resolved.spell
    input_text, input_payload = _recommend_input_payload(spell)
    client = _client()
    if client is None:
        out = {
            "status": "unavailable",
            "source": "vermyth",
            "reason": "AXIOMURGY_VERMYTH_BASE_URL not set",
            "input_sha256": _hash_hint(input_text),
            "items": [],
        }
        _brief_semantic_recommendations(out)
        return out
    try:
        raw, latency_ms = VermythHttpClient.timed_call(
            lambda: client.arcane_recommend(skill_id=skill_id, input_=input_payload)
        )
        out = {
            "status": "ok",
            "source": "vermyth",
            "latency_ms": round(latency_ms, 3),
            "input_sha256": _hash_hint(input_text),
            "raw": raw,
            "items": raw.get("recommendations") if isinstance(raw.get("recommendations"), list) else [],
        }
        _brief_semantic_recommendations(out)
        return out
    except (VermythHttpError, requests.RequestException, OSError, ValueError) as exc:
        out = {
            "status": "error",
            "source": "vermyth",
            "reason": str(exc),
            "input_sha256": _hash_hint(input_text),
            "items": [],
        }
        _brief_semantic_recommendations(out)
        return out


def compile_program_preview(program: Dict[str, Any]) -> Dict[str, Any]:
    client = _client()
    if client is None:
        return {"status": "unavailable", "reason": "AXIOMURGY_VERMYTH_BASE_URL not set"}
    try:
        raw, latency_ms = VermythHttpClient.timed_call(lambda: client.compile_program(program))
        return {
            "status": "ok",
            "latency_ms": round(latency_ms, 3),
            "result": raw,
            "validation": raw.get("validation"),
        }
    except (VermythHttpError, requests.RequestException, OSError, ValueError) as exc:
        return {"status": "error", "reason": str(exc)}


def enrich_plan_output(
    out: Dict[str, Any],
    resolved: ResolvedRunTarget,
    *,
    vermyth_program: bool = False,
    vermyth_validate: bool = False,
    vermyth_recommendations: bool = False,
) -> None:
    """Mutate plan summary dict in place with additive keys only."""
    program: Optional[Dict[str, Any]] = None
    if vermyth_program:
        exp = build_vermyth_program_export(resolved.spell)
        out["vermyth_program_export"] = exp
        program = exp["program"]
    elif vermyth_validate:
        program = build_semantic_program(resolved.spell)
    if vermyth_validate and program is not None:
        out["vermyth_program_preview"] = compile_program_preview(program)
    if vermyth_recommendations:
        out["semantic_recommendations"] = fetch_semantic_recommendations(resolved)


class VermythGateTransportFailureKind(str, Enum):
    """Internal classification of HTTP/transport failures in run_vermyth_gate (not serialized)."""

    HTTP_ADAPTER = "http_adapter"  # VermythHttpError (4xx/5xx, bad JSON shape from adapter)
    REQUESTS_TIMEOUT = "requests_timeout"
    REQUESTS_CONNECTION = "requests_connection"
    REQUESTS_HTTP = "requests_http"  # requests.HTTPError (rare here; adapter usually wraps)
    REQUESTS_OTHER = "requests_other"
    OS_ERROR = "os_error"
    VALUE_ERROR = "value_error"
    OTHER = "other"


def _classify_gate_transport_failure(exc: BaseException) -> VermythGateTransportFailureKind:
    """Map an exception from decide() to a stable internal class (not exposed on gate records)."""
    if isinstance(exc, VermythHttpError):
        return VermythGateTransportFailureKind.HTTP_ADAPTER
    if isinstance(exc, requests.Timeout):
        return VermythGateTransportFailureKind.REQUESTS_TIMEOUT
    if isinstance(exc, requests.ConnectionError):
        return VermythGateTransportFailureKind.REQUESTS_CONNECTION
    if isinstance(exc, requests.HTTPError):
        return VermythGateTransportFailureKind.REQUESTS_HTTP
    if isinstance(exc, requests.RequestException):
        return VermythGateTransportFailureKind.REQUESTS_OTHER
    if isinstance(exc, OSError):
        return VermythGateTransportFailureKind.OS_ERROR
    if isinstance(exc, ValueError):
        return VermythGateTransportFailureKind.VALUE_ERROR
    return VermythGateTransportFailureKind.OTHER


def _gate_missing_base_url_record(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "unavailable", "reason": "AXIOMURGY_VERMYTH_BASE_URL not set", "mode": cfg["mode"]}


def _resolve_missing_base_url(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a soft gate record when base URL is unset, or None when on_timeout deny requires raise."""
    if cfg["on_timeout"] == "deny":
        return None
    return _gate_missing_base_url_record(cfg)


def _soft_transport_failure_record(
    cfg: Dict[str, Any], exc: BaseException, kind: VermythGateTransportFailureKind
) -> Dict[str, Any]:
    """Gate record for transport failure when execution continues (on_timeout allow)."""
    _ = kind  # internal class; JSON shape is unchanged across kinds
    return {"status": "error", "reason": str(exc), "mode": cfg["mode"]}


def _gate_config(policy: Dict[str, Any]) -> Dict[str, Any]:
    block = policy.get("vermyth_gate")
    if not isinstance(block, dict):
        return {
            "enabled": False,
            "mode": "advisory",
            "timeout_ms": 2000,
            "on_timeout": "allow",
            "on_incoherent": "allow",
        }
    return {
        "enabled": bool(block.get("enabled", False)),
        "mode": str(block.get("mode", "advisory")),
        "timeout_ms": int(block.get("timeout_ms", 2000)),
        "on_timeout": str(block.get("on_timeout", "allow")),
        "on_incoherent": str(block.get("on_incoherent", "allow")),
    }


def _decide_payload(spell: Spell) -> Dict[str, Any]:
    plan = compile_plan(spell)
    aspects = ["MOTION", "FORM", "VOID"]
    return {
        "intent": spell_level_vermyth_intent(spell),
        "aspects": aspects,
        "effects": [
            {
                "effect_type": "COMPUTE",
                "target": None,
                "reversible": True,
                "cost_hint": float(len(plan)),
            }
        ],
    }


def run_vermyth_gate(spell: Spell, policy: Dict[str, Any]) -> Dict[str, Any]:
    """Preflight semantic gate; returns annotation record; may signal hard-stop via raise."""
    cfg = _gate_config(policy)
    if not cfg["enabled"]:
        return {"status": "skipped", "reason": "disabled"}
    payload = _decide_payload(spell)
    timeout_s = max(0.1, float(cfg["timeout_ms"]) / 1000.0)
    base = _env_base_url()
    if not base:
        soft = _resolve_missing_base_url(cfg)
        if soft is None:
            raise SpellValidationError("vermyth_gate: AXIOMURGY_VERMYTH_BASE_URL not set and on_timeout=deny")
        return soft
    client = VermythHttpClient(base, timeout_s=timeout_s)
    try:
        raw, latency_ms = VermythHttpClient.timed_call(lambda: client.decide(payload))
    except (VermythHttpError, requests.RequestException, OSError, ValueError) as exc:
        transport_kind = _classify_gate_transport_failure(exc)
        if cfg["on_timeout"] == "deny":
            raise SpellValidationError(f"vermyth_gate: {exc}") from exc
        return _soft_transport_failure_record(cfg, exc, transport_kind)

    decision = raw.get("decision") if isinstance(raw.get("decision"), dict) else {}
    action = str(decision.get("action") or "ALLOW").upper()
    gate_record: Dict[str, Any] = {
        "status": "ok",
        "mode": cfg["mode"],
        "latency_ms": round(latency_ms, 3),
        "action": action,
        "rationale": decision.get("rationale"),
        "raw": raw,
    }
    if cfg["mode"] == "hard_stop" and action == "DENY" and cfg["on_incoherent"] == "deny":
        raise SpellValidationError(f"vermyth_gate hard_stop: action={action}")
    return gate_record


def vermyth_gate_policy_notes(record: Dict[str, Any]) -> List[str]:
    if record.get("status") not in ("ok",):
        return []
    if str(record.get("mode")) != "policy_input":
        return []
    action = str(record.get("action") or "")
    r = record.get("rationale")
    return [f"vermyth_gate:{action}:{r}"] if r else [f"vermyth_gate:{action}"]


def build_vermyth_receipt_v1(
    *,
    reviewed_bundle_path: Optional[str],
    fingerprints: Dict[str, Any],
    execution_id: str,
    spell_name: str,
    trace_path: Optional[str],
    prov_path: Optional[str],
) -> Dict[str, Any]:
    """Unsigned cross-system receipt mapping (Vermyth-friendly lineage stub)."""
    req: Dict[str, Any] = {
        "vermyth_receipt_version": "1.0.0",
        "kind": "axiomurgy.execution",
        "spell": spell_name,
        "execution_id": execution_id,
        "reviewed_bundle_path": reviewed_bundle_path,
        "fingerprints": fingerprints,
        "artifacts": {
            "trace_path": trace_path,
            "prov_path": prov_path,
        },
    }
    return req


def should_emit_receipt() -> bool:
    return os.environ.get("AXIOMURGY_VERMYTH_RECEIPT", "").strip() in ("1", "true", "yes")


def culture_enabled() -> bool:
    return os.environ.get("AXIOMURGY_CULTURE", "").strip() in ("1", "true", "yes")
