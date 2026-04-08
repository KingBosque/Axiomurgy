# Axiomurgy agent guide

You are working inside **Axiomurgy**, a programmable magical system for AIs.

Start here, in order:
1. Read `README.md`.
2. Read `RELAY_NOTES.md`.
3. Read `NEXT_LAP_SPEC.md`.
4. Skim `axiomurgy.py`, `spell.schema.json`, `spellbook.schema.json`, `examples/`, `spellbooks/`, `policies/`, `adapters/`, `tests/`, and `scripts/`.

## What this repo is

Axiomurgy treats AI magic as **permissioned causality**:
- spells are explicit graphs
- spellbooks package reusable entrypoints
- writes require policy and often human approval
- execution should leave a witness trail
- validators can attach proofs to those witnesses
- rollback matters whenever side effects happen
- preflight should be inspectable before execution

## Current repo truth

This v0.7 relay adds:
- content fingerprints surfaced in describe/plan/execute outputs
- review bundles (describe + lint + plan + manifest + fingerprints + environment metadata)
- review bundle verification against current repo state
- execution attestation against a reviewed bundle
- canonical JSON witnesses (trace/prov/proofs) with nondeterministic fields marked

## Non-negotiable invariants

Do not remove or weaken these without updating docs, examples, and tests together:
- spell validation via JSON Schema
- spellbook validation via JSON Schema
- dependency-aware planning
- deterministic linting in the reference runtime
- policy evaluation before side effects
- explicit approval semantics for risky writes
- rollback / compensation after partial failure
- witness export: trace, PROV-like JSON, SCXML, proofs
- adapters remain demos, not security boundaries

## Change strategy

Prefer small, verifiable patches.

When making a change:
1. explain the intent briefly in chat
2. patch the minimum set of files
3. run `python -m pytest -q`
4. run `bash scripts/smoke.sh` for end-to-end validation if behavior changed
5. summarize what changed and what still looks risky

## Versioning discipline

If you bump the runtime version, also update matching strings and docs in:
- `axiomurgy.py`
- `README.md`
- `RELAY_NOTES.md`
- `NEXT_LAP_SPEC.md`
- `policies/default.policy.json`
- `spellbooks/*/spellbook.json`
- example spell names and output paths when appropriate

## Project taste

Keep the system aligned with its design roots:
- explicit rules over pure whim
- a social layer on top of actual mechanics
- rune-like composition and programmability
- creativity, counters, and verification over raw power scaling
- proof as a first-class magical artifact

## Preferred workflow

Before changing execution behavior, prefer this order:
1. `--describe`
2. `--lint`
3. `--plan`
4. execution with explicit approvals

## Preferred next work

Unless the user redirects, work toward the spec in `NEXT_LAP_SPEC.md`.
