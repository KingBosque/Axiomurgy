"""Named evaluation modes = explicit env flag sets (no ad hoc juggling)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, Tuple

# Env vars that affect the advisory reasoning stack for this harness.
MODE_ENV_KEYS: Tuple[str, ...] = (
    "AXIOMURGY_REASONING",
    "AXIOMURGY_REASONING_EXPERIMENTAL",
    "AXIOMURGY_REASONING_GENERATION",
    "AXIOMURGY_REASONING_LULLIAN",
    "AXIOMURGY_WYRD",
)

# mode_name -> explicit values for keys that must be SET. Keys not listed are REMOVED from os.environ.
EVAL_MODES: Dict[str, Dict[str, str]] = {
    "baseline": {},
    "core_reasoning": {"AXIOMURGY_REASONING": "1"},
    "experimental_structure": {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
    },
    "generation_only": {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
        "AXIOMURGY_REASONING_GENERATION": "1",
    },
    "generation_ranked": {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
        "AXIOMURGY_REASONING_GENERATION": "1",
        "AXIOMURGY_REASONING_LULLIAN": "1",
    },
    "generation_ranked_wyrd": {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
        "AXIOMURGY_REASONING_GENERATION": "1",
        "AXIOMURGY_REASONING_LULLIAN": "1",
        "AXIOMURGY_WYRD": "1",
    },
}


def mode_flags_snapshot(mode: str) -> Dict[str, Any]:
    """Human-readable flag snapshot after applying the mode (for reports)."""
    cfg = EVAL_MODES.get(mode)
    if cfg is None:
        raise KeyError(f"unknown mode: {mode!r}")
    out: Dict[str, Any] = {"mode": mode}
    for k in MODE_ENV_KEYS:
        if k in cfg:
            out[k] = cfg[k]
        else:
            out[k] = None
    return out


def _apply_mode_dict(mode: str) -> None:
    cfg = EVAL_MODES[mode]
    for k in MODE_ENV_KEYS:
        if k in cfg:
            os.environ[k] = cfg[k]
        else:
            os.environ.pop(k, None)


def _snapshot_env() -> Dict[str, str | None]:
    return {k: os.environ.get(k) for k in MODE_ENV_KEYS}


def _restore_env(saved: Mapping[str, str | None]) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@contextmanager
def apply_eval_mode(mode: str) -> Iterator[None]:
    """Temporarily set env for one evaluation mode; restore previous values after."""
    if mode not in EVAL_MODES:
        raise KeyError(f"unknown mode: {mode!r}")
    saved = _snapshot_env()
    try:
        _apply_mode_dict(mode)
        yield
    finally:
        _restore_env(saved)


def all_mode_names() -> Tuple[str, ...]:
    return tuple(sorted(EVAL_MODES.keys()))
