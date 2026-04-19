"""Optional plan/describe reasoning blocks (AXIOMURGY_REASONING=1)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .legacy import ResolvedRunTarget
from .planning import build_reasoning_plan_context
from . import combinatorics
from . import correspondence
from . import dialectic
from . import friction
from . import generation as generation_mod
from . import governor
from . import lullian
from . import habitus
from . import scene
from . import telos
from .wyrd.store import build_wyrd_hints

# Bump when the JSON shape or classification contract changes.
REASONING_VERSION = "1.7.0"

REASONING_EXPERIMENTAL_BLOCK_KEYS = (
    "candidate_verification",
    "correspondence",
    "friction",
    "combinatorics_search",
    "wyrd_hints",
    "generation_candidates",
)

# Top-level reasoning keys considered "derived" for classification (stable contract).
DERIVED_KEYS_MINIMAL = ("governor", "telos", "scene", "dialectic", "habitus")


def _derived_keys_for_classification(*, experimental: bool) -> list[str]:
    """Sorted list: experimental mode is a strict superset (adds the experimental container key)."""
    keys = list(DERIVED_KEYS_MINIMAL)
    if experimental:
        keys.append("experimental")
    return sorted(keys)


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
        "derived_keys": _derived_keys_for_classification(experimental=experimental),
        "habitus_role": "descriptive_context",
        "experimental_enabled": experimental,
        "experimental_keys": sorted(REASONING_EXPERIMENTAL_BLOCK_KEYS) if experimental else [],
    }


def _wyrd_hints_disabled() -> Dict[str, Any]:
    return {
        "kind": "derived",
        "recent_nodes": [],
        "recent_edges": [],
        "related_prior_runs": [],
        "consistency_notes": ["wyrd_disabled"],
    }


def _generation_candidates_disabled() -> Dict[str, Any]:
    return {
        "kind": "derived",
        "bounded": True,
        "review_required": True,
        "candidates": [],
        "generation_enabled": False,
    }


def _build_experimental_core(
    resolved: ResolvedRunTarget,
    plan_context: Dict[str, Any],
    telos_view: Dict[str, Any],
    governor_view: Dict[str, Any],
    dialectic_view: Dict[str, Any],
) -> Dict[str, Any]:
    """Flat map only: no nested classification / maturity taxonomies under experimental."""
    spell = resolved.spell
    return {
        "correspondence": correspondence.build_correspondence(spell, plan_context, telos_view),
        "friction": friction.build_friction(spell, plan_context, telos_view, governor_view, dialectic_view),
        "combinatorics_search": combinatorics.build_combinatorics_search(spell),
    }


def build_reasoning_payload(resolved: ResolvedRunTarget, plan_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    spell = resolved.spell
    experimental = reasoning_experimental_enabled()
    if plan_summary is not None:
        plan_ctx = {
            "steps": plan_summary.get("steps") or [],
            "write_steps": plan_summary.get("write_steps") or [],
            "required_approvals": plan_summary.get("required_approvals") or [],
            "external_calls": plan_summary.get("external_calls") or [],
        }
    else:
        plan_ctx = build_reasoning_plan_context(resolved)
    telos_view = telos.build_telos(spell, plan_ctx)
    governor_view = governor.build_governor_view(resolved, plan_ctx)
    dialectic_view = dialectic.build_dialectic_trace(spell, plan_ctx, telos_view, governor_view)
    out: Dict[str, Any] = {
        "axiomurgy_reasoning_version": REASONING_VERSION,
        "classification": _build_classification(experimental=experimental),
        "governor": governor_view,
        "telos": telos_view,
        "dialectic": dialectic_view,
        "scene": scene.build_scene(spell),
        "habitus": habitus.build_habitus(resolved),
    }
    if experimental:
        ex = _build_experimental_core(resolved, plan_ctx, telos_view, governor_view, dialectic_view)
        out["experimental"] = ex
        run_id_snap: Optional[str] = None
        if wyrd_persistence_enabled() and plan_summary is not None:
            try:
                from .wyrd.snapshot import append_reasoning_snapshot

                run_id_snap = append_reasoning_snapshot(resolved, plan_summary, out)
            except Exception:
                run_id_snap = None
        if wyrd_persistence_enabled():
            ex["wyrd_hints"] = build_wyrd_hints(
                resolved.artifact_dir,
                spell_name=spell.name,
                current_run_id=run_id_snap,
            )
        else:
            ex["wyrd_hints"] = _wyrd_hints_disabled()
        if generation_mod.reasoning_generation_enabled():
            ex["generation_candidates"] = generation_mod.build_parthenogenesis_candidates(
                resolved,
                plan_ctx,
                out,
                wyrd_hints=ex["wyrd_hints"],
                run_id=run_id_snap or "",
                plan_summary=plan_summary,
            )
            if lullian.reasoning_lullian_enabled():
                ex["candidate_verification"] = lullian.build_candidate_verification(
                    resolved, plan_ctx, out, plan_summary=plan_summary
                )
        else:
            ex["generation_candidates"] = _generation_candidates_disabled()
    return out


def attach_reasoning_to_plan(plan_out: Dict[str, Any], resolved: ResolvedRunTarget) -> None:
    if not reasoning_enabled():
        return
    plan_out["reasoning"] = build_reasoning_payload(resolved, plan_summary=plan_out)


def attach_reasoning_to_describe(describe_out: Dict[str, Any], resolved: ResolvedRunTarget) -> None:
    if not reasoning_enabled():
        return
    describe_out["reasoning"] = build_reasoning_payload(resolved)
