"""Fingerprint and input/output manifest helpers."""

from .legacy import (
    classify_input_manifest,
    compute_spell_fingerprints,
    compute_spellbook_fingerprints,
    extract_declared_input_paths,
    extract_output_schema_paths,
)

__all__ = [
    "extract_declared_input_paths",
    "classify_input_manifest",
    "extract_output_schema_paths",
    "compute_spell_fingerprints",
    "compute_spellbook_fingerprints",
]
