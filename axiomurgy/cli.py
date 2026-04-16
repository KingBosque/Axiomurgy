"""Command-line entry points for the runtime."""

from .legacy import (
    _load_run_manifest_for_replay,
    _revolution_dir_from_run_manifest,
    main,
    parse_args,
)

__all__ = [
    "parse_args",
    "_load_run_manifest_for_replay",
    "_revolution_dir_from_run_manifest",
    "main",
]
