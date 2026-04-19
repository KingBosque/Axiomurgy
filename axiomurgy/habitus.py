"""Bourdieu habitus: context / priors (advisory; complements culture store)."""

from __future__ import annotations

from typing import Any, Dict

from .legacy import ResolvedRunTarget


def build_habitus(resolved: ResolvedRunTarget) -> Dict[str, Any]:
    """Descriptive context only (paths, not policy); not a second policy engine."""
    return {
        "kind": "descriptive_context",
        "artifact_dir": str(resolved.artifact_dir),
        "policy_path": str(resolved.policy_path),
    }
