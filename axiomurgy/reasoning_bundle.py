"""Optional plan/describe reasoning blocks (AXIOMURGY_REASONING=1)."""

from __future__ import annotations

import os
from typing import Any, Dict

from .legacy import ResolvedRunTarget
from . import combinatorics
from . import correspondence
from . import dialectic
from . import friction
from . import generation
from . import governor
from . import habitus
from . import scene
from . import telos
from .wyrd.store import read_wyrd_hints

# Bump when the JSON shape or classification contract changes.
REASONING_VERSION = "1.1.0"

_EXPERIMENTAL_BLOCK_KEYS = [
    "correspondence",
    "friction",
    "combinatorics_search",
    "wyrd_hints",
    "generation_candidates",
]


def reasoning_enabled() -> bool:
    v = os.environ.get("AXIOMURGY_REASONING", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def reasoning_experimental_enabled() -> bool:
    """Phase-advanced / exploratory blocks under reasoning.experimental.*"""
    v = os.environ.get("AXIOMURGY_REASONING_EXPERIMENTAL", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def wyrd_persistence_enabled() -> bool:
    v = os.environ.get("AXIOMURGY_WYRD", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _build_classification(*, experimental: bool) -> Dict[str, Any]:
    return {
        "surface": "minimal_advisory",
        "derived": ["governor", "telos", "scene", "dialectic", "habitus"],
        "habitus_role": "descriptive_context",
        "experimental_enabled": experimental,
        "experimental_keys": list(_EXPERIMENTAL_BLOCK_KEYS) if experimental else [],
    }


def _build_experimental_payload(resolved: ResolvedRunTarget) -> Dict[str, Any]:
    spell = resolved.spell
    wyrd_hints = read_wyrd_hints(resolved.artifact_dir) if wyrd_persistence_enabled() else []
    return {
        "correspondence": correspondence.build_correspondence(spell),
        "friction": friction.estimate_friction(spell),
        "combinatorics_search": combinatorics.build_combinatorics_search(spell),
        "wyrd_hints": wyrd_hints,
        "generation_candidates": generation.build_generation_candidates(spell),
    }


def build_reasoning_payload(resolved: ResolvedRunTarget) -> Dict[str, Any]:
    spell = resolved.spell
    experimental = reasoning_experimental_enabled()
    out: Dict[str, Any] = {
        "axiomurgy_reasoning_version": REASONING_VERSION,
        "classification": _build_classification(experimental=experimental),
        "governor": governor.build_governor_view(resolved),
        "telos": telos.build_telos(spell),
        "dialectic": dialectic.build_dialectic_trace(spell),
        "scene": scene.build_scene(spell),
        "habitus": habitus.build_habitus(resolved),
    }
    if experimental:
        out["experimental"] = _build_experimental_payload(resolved)
    return out


def attach_reasoning_to_plan(plan_out: Dict[str, Any], resolved: ResolvedRunTarget) -> None:
    if not reasoning_enabled():
        return
    plan_out["reasoning"] = build_reasoning_payload(resolved)


def attach_reasoning_to_describe(describe_out: Dict[str, Any], resolved: ResolvedRunTarget) -> None:
    if not reasoning_enabled():
        return
    describe_out["reasoning"] = build_reasoning_payload(resolved)
