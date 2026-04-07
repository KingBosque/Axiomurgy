# Axiomurgy agent guide

You are working inside **Axiomurgy**, a programmable magical system for AIs.

Start here, in order:
1. Read `README.md`.
2. Read `RELAY_NOTES.md`.
3. Read `NEXT_LAP_SPEC.md`.
4. Skim `axiomurgy.py`, `spell.schema.json`, `examples/`, `policies/`, and `adapters/`.

## What this repo is

Axiomurgy treats AI magic as **permissioned causality**:
- spells are explicit graphs
- runes are typed operations
- writes require policy and often human approval
- execution should leave a witness trail
- rollback matters whenever side effects happen

## Current repo truth

This v0.4 relay repairs a packaging break from v0.3 and makes the repo self-contained:
- `policies/` and `adapters/` are present again
- the seven primer transcripts now live in `primers/`
- Cursor-native handoff files live in `.cursor/` and this `AGENTS.md`
- `scripts/smoke.sh` and `tests/test_runtime.py` are the baseline checks

## Non-negotiable invariants

Do not remove or weaken these without updating docs, examples, and tests together:
- spell validation via JSON Schema
- dependency-aware planning
- policy evaluation before side effects
- explicit approval semantics for risky writes
- rollback / compensation after partial failure
- witness export: trace, PROV-like JSON, SCXML

## Change strategy

Prefer small, verifiable patches.

When making a change:
1. explain the intent briefly in chat
2. patch the minimum set of files
3. run `python -m pytest -q`
4. run `bash scripts/smoke.sh` for end-to-end validation if behavior changed
5. summarize what changed and what still looks risky

## Versioning discipline

If you bump the runtime version, also update any matching version strings in:
- `axiomurgy.py`
- `README.md`
- `RELAY_NOTES.md`
- `policies/default.policy.json`
- `adapters/` files if they embed versions
- example spell names and output paths when appropriate

## Project taste

Keep the system aligned with its design roots:
- explicit rules over pure whim
- a social layer on top of actual mechanics
- rune-like composition and programmability
- creativity, counters, and verification over raw power scaling

## Preferred next work

Unless the user redirects, work toward the spec in `NEXT_LAP_SPEC.md`.
