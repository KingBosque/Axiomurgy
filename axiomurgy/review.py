"""Review bundle build/compare and execution attestation."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from .legacy import ResolvedRunTarget

from .describe import describe_target, environment_metadata, lint_target
from .planning import build_plan_summary

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

