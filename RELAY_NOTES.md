# Axiomurgy v0.4 relay notes

What this lap adds:
- fixes the broken v0.3 package by restoring `policies/` and `adapters/`
- makes the repo self-contained by copying the seven primer transcripts into `primers/`
- adds a real install file in `requirements.txt`
- adds fast tests and an end-to-end smoke script
- adds Cursor-native handoff files: `AGENTS.md`, `.cursor/rules/`, `.cursor/environment.json`
- defines the next milestone in `NEXT_LAP_SPEC.md`

Windows note: the runtime resolves spell-relative paths for `mirror.read`, uses `sys.executable` for MCP `python`/`python3` server commands, sets `PYTHONIOENCODING=utf-8` (and UTF-8 stdio in the demo MCP server) so JSON-RPC over pipes does not hit `cp1252`/`UnicodeEncodeError` on primer content, stores the mock issue DB and MCP workspace under the repo root (`axiomurgy_mock_issues.json`, `axiomurgy_workspace/`), and the README uses PowerShell for quick start.

Verified demos in this relay:
- `examples/primer_to_axioms.spell.json`
- `examples/primer_via_mcp.spell.json`
- `examples/openapi_ticket_then_fail.spell.json`

Why this lap exists:
- Cursor can do better work when the repo is self-describing
- the previous zip had enough structure to inspire coding but not enough guidance to steer it consistently
- fixing packaging and adding repeatable checks is a better relay move than piling on one more speculative feature

Suggested next relay:
- spellbook package format
- validator runes and proof-carrying witnesses
- richer final-result summaries for downstream agents
- stronger docs around safety boundaries and trusted execution
