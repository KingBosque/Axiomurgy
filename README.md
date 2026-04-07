# Axiomurgy v0.5

Axiomurgy is a programmable magical system for AIs.

This relay turns the runtime from a loose set of example spells into a **packaged spellbook system** with **proof-carrying witnesses**.

## What changed in v0.5

- added `spellbook.schema.json`
- added spellbook loading and entrypoint resolution to the runtime
- added deterministic validator runes:
  - `seal.assert_jsonschema`
  - `seal.assert_markers`
  - `seal.assert_contains_sections`
  - `seal.assert_path_exists`
- added proof summaries to runtime results, trace output, PROV-like witness output, and standalone `.proofs.json` files
- added a packaged spellbook example at `spellbooks/primer_codex/`
- updated direct examples to `v0_5`
- expanded tests and smoke coverage around spellbooks and proofs

## Core features

- JSON Schema spell validation
- spellbook packaging with manifest-driven entrypoints
- dependency-aware execution planning
- policy checks and human approval gates
- rollback and compensation semantics
- PROV-like witness export and SCXML plan export
- proof-carrying witness summaries
- MCP stdio resource and tool integration
- OpenAPI-driven HTTP calls
- lightweight confidence and entropy tracking

## Repository map

- `axiomurgy.py` — reference runtime
- `spell.schema.json` — spell contract
- `spellbook.schema.json` — spellbook manifest contract
- `examples/` — direct runnable spells (plus smaller smoke spells such as `research_brief.spell.json` / `inbox_triage.spell.json` when present)
- `spellbooks/primer_codex/` — packaged proof-carrying spellbook
- `primers/` — local copies of the seven primer transcripts
- `adapters/` — demo MCP and OpenAPI servers
- `policies/` — default runtime policy
- `artifacts/` — generated outputs for direct examples (gitignored)
- `axiomurgy_workspace/` — MCP demo staging (gitignored)
- `scripts/smoke.sh` — end-to-end verification (Git Bash / WSL on Windows)
- `tests/test_runtime.py` — regression checks
- `AGENTS.md` and `.cursor/rules/` — Cursor handoff

## Install

From the repository root:

```powershell
python -m pip install -r requirements.txt
```

## Quick start (Windows PowerShell)

Run the direct primer relay:

```powershell
python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish
```

Run the packaged spellbook entrypoint:

```powershell
python axiomurgy.py spellbooks/primer_codex --approve publish
```

Run the MCP relay:

```powershell
python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage
```

Run the OpenAPI rollback demo (mock server in one terminal, spell in another):

```powershell
python adapters/mock_issue_server.py
```

```powershell
python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket
```

That final command is expected to return `status: failed`, because the spell intentionally triggers a post-write failure in order to exercise compensation.

## Result shape

Successful runs now include a `proofs` block:

```json
{
  "status": "succeeded",
  "proofs": {
    "total": 4,
    "passed": 4,
    "failed": 0,
    "by_validator": {
      "seal.assert_contains_sections": 1,
      "seal.assert_jsonschema": 1,
      "seal.assert_markers": 1,
      "seal.assert_path_exists": 1
    }
  }
}
```

Each run also emits:

- `*.trace.json`
- `*.prov.json`
- `*.scxml`
- `*.proofs.json`

## Validation

Fast tests:

```powershell
python -m pytest -q
```

Full smoke (requires `bash`, e.g. Git Bash or WSL):

```bash
bash scripts/smoke.sh
```

## Cursor workflow

If you are using Cursor:

- start with `AGENTS.md`
- let project rules load from `.cursor/rules/`
- use `CURSOR_PROMPTS.md` for ready-to-paste tasks
- use `.cursor/environment.json` as the starter background-agent install config

## Current project direction

The next relay is defined in `NEXT_LAP_SPEC.md`.
The short version: move from packaged execution toward **plan mode, linting, and approval manifests**.
