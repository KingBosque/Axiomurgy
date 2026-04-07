# Axiomurgy v0.5 relay notes

Windows note: spell-relative paths for `mirror.read` (including `file://`), MCP subprocess uses `sys.executable` for `python`/`python3`, `PYTHONIOENCODING=utf-8` for JSON-RPC over pipes, demo MCP server uses UTF-8 stdio and `axiomurgy_workspace/`, mock issue DB defaults to the repo root (`axiomurgy_mock_issues.json`). README quick start uses PowerShell.

What this lap adds:
- spellbook manifests and `spellbook.schema.json`
- runtime support for packaged spellbook entrypoints
- deterministic validator runes
- proof summaries in runtime results and witness files
- a packaged `spellbooks/primer_codex/` example
- expanded tests and smoke coverage for packaged execution

Verified demos in this relay:
- `examples/primer_to_axioms.spell.json`
- `examples/primer_via_mcp.spell.json`
- `examples/openapi_ticket_then_fail.spell.json`
- `spellbooks/primer_codex/`

Why this lap exists:
- direct spell files were useful, but they did not yet provide a clean packaging story
- witness files existed, but there was no compact proof surface for downstream agents or IDEs
- Cursor and other relay agents benefit from packaged entrypoints and deterministic preflight checks

Suggested next relay:
- plan mode for spells and spellbooks
- deterministic linting
- approval manifests
- better preflight summaries for downstream agents and IDEs
