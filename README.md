# Axiomurgy v0.4

Axiomurgy is a programmable magical system for AIs.

This relay does two things:

1. repairs the broken v0.3 package so the repo is runnable again
2. adds Cursor-native scaffolding so Cursor can carry the next lap with less prompt babysitting

## What changed in v0.4

- restored missing `policies/` and `adapters/` directories
- copied the seven uploaded primer transcripts into `primers/` so the repo is self-contained
- renamed the demo outputs to `v0_4`
- added `requirements.txt`
- added `scripts/smoke.sh` and `tests/test_runtime.py`
- added `AGENTS.md`, `.cursor/rules/`, and `.cursor/environment.json`
- wrote a concrete next-milestone brief in `NEXT_LAP_SPEC.md`

## Core features

- JSON Schema spell validation
- dependency-aware execution planning
- policy checks and human approval gates
- rollback and compensation semantics
- PROV-like witness export and SCXML plan export
- MCP stdio resource and tool integration
- OpenAPI-driven HTTP calls
- lightweight confidence and entropy tracking

## Repository map

- `axiomurgy.py` — reference runtime
- `spell.schema.json` — spell contract
- `examples/` — runnable demo spells (plus smaller smoke spells `research_brief.spell.json` and `inbox_triage.spell.json` compatible with this schema)
- `primers/` — local copies of the seven primer transcripts
- `adapters/` — demo MCP and OpenAPI servers
- `policies/` — default runtime policy
- `artifacts/` — generated outputs and witnesses
- `axiomurgy_workspace/` — MCP demo staging (gitignored)
- `scripts/smoke.sh` — end-to-end verification (Git Bash / WSL on Windows)
- `tests/test_runtime.py` — fast regression checks
- `AGENTS.md` and `.cursor/rules/` — Cursor handoff

## Install

From the repository root:

```powershell
python -m pip install -r requirements.txt
```

## Quick start (Windows PowerShell)

Run the direct-file primer relay:

```powershell
python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish
```

Run the MCP relay:

```powershell
python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage
```

Run the OpenAPI rollback demo (start the mock server in a second terminal, then run the spell):

```powershell
python adapters/mock_issue_server.py
```

```powershell
python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket
```

That final command is expected to return a result whose spell status is `failed`, because the spell intentionally triggers a post-write failure in order to exercise compensation.

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
The short version: move from loose examples toward **spellbooks + stronger validators + proof-carrying witnesses**.
