"""Ouroboros chamber subsystem and replay helpers."""

from . import legacy as _legacy
from .legacy import (
    _admissibility_status_rank,
    evaluate_acceptance_contract,
    expand_cycle_proposals,
    load_cycle_config,
    ouroboros_chamber,
    plan_ouroboros_proposals,
    proposal_id,
    replay_ouroboros_revolution,
    write_ouroboros_proposal_plan,
    write_ouroboros_run_manifest,
)


def __getattr__(name: str):
    if "ouroboros" in name or name.startswith("_format_") or name.startswith("_score_"):
        return getattr(_legacy, name)
    raise AttributeError(name)


__all__ = [
    "load_cycle_config",
    "expand_cycle_proposals",
    "proposal_id",
    "evaluate_acceptance_contract",
    "plan_ouroboros_proposals",
    "write_ouroboros_proposal_plan",
    "replay_ouroboros_revolution",
    "write_ouroboros_run_manifest",
    "ouroboros_chamber",
    "_admissibility_status_rank",
]
