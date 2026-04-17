"""Proof normalization and summary helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set

from .util import utc_now


def normalize_proof(proof: Dict[str, Any], default_validator: str = "", default_target: str = "") -> Dict[str, Any]:
    return {
        "validator": str(proof.get("validator") or default_validator or "unknown"),
        "target": str(proof.get("target") or default_target or "unknown"),
        "status": str(proof.get("status") or "unknown"),
        "message": str(proof.get("message") or ""),
        "evidence": proof.get("evidence"),
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


__all__ = [
    "normalize_proof",
    "build_proof",
    "extract_proofs",
    "build_proof_summary",
]
