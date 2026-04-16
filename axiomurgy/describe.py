"""Describe, lint, and environment metadata workflows."""

from .legacy import (
    build_lint_issue,
    describe_target,
    environment_metadata,
    iter_schema_issues,
    lint_spell_file,
    lint_spellbook,
    lint_target,
)

__all__ = [
    "describe_target",
    "build_lint_issue",
    "iter_schema_issues",
    "lint_spell_file",
    "lint_spellbook",
    "lint_target",
    "environment_metadata",
]
