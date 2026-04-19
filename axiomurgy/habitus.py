"""Bourdieu habitus: context / priors (advisory; complements culture store)."""

from __future__ import annotations

from typing import Any, Dict

from .legacy import ResolvedRunTarget


def build_habitus(resolved: ResolvedRunTarget) -> Dict[str, Any]:
    """Environment posture for reasoning; deterministic subset."""
    return {
        "artifact_dir": str(resolved.artifact_dir),
        "policy_path": str(resolved.policy_path),
        "posture": "advisory",
    }
