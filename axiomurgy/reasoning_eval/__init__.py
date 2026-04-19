"""Advisory reasoning efficacy evaluation harness (offline; not execution authority)."""

from __future__ import annotations

from .capture import capture_plan_reasoning
from .corpus import load_corpus, resolve_corpus_spell_path
from .labels import load_labels
from .metrics import aggregate_metrics, compute_cross_mode_metrics
from .modes import EVAL_MODES, MODE_ENV_KEYS, apply_eval_mode, mode_flags_snapshot
from .run import run_evaluation

__all__ = [
    "EVAL_MODES",
    "MODE_ENV_KEYS",
    "apply_eval_mode",
    "aggregate_metrics",
    "capture_plan_reasoning",
    "compute_cross_mode_metrics",
    "load_corpus",
    "load_labels",
    "mode_flags_snapshot",
    "resolve_corpus_spell_path",
    "run_evaluation",
]
