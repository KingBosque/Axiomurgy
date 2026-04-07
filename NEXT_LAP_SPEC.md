# Axiomurgy v0.5 target

## Working title

**Spellbooks and proof-carrying witnesses**

## Why this should be next

Axiomurgy already has the bones of a programmable magic runtime:
- graph execution
- policy and approvals
- rollback
- witnesses
- adapters

What it does not yet have is a strong packaging story or a strong proof story.
Right now a spell can run and emit witnesses, but the runtime does not clearly distinguish between:
- a spell artifact
- a reusable spell collection
- a validated claim about output quality

That is the gap for v0.5.

## Scope

### 1. Spellbook package format

Introduce a spellbook directory format with a manifest.

Suggested layout:

```text
spellbooks/
  primer_codex/
    spellbook.json
    spells/
      publish_codex.spell.json
    schemas/
    docs/
    tests/
```

Suggested manifest fields:
- `name`
- `version`
- `description`
- `entrypoints`
- `required_capabilities`
- `default_policy`
- `validators`
- `artifacts_dir`

Runtime should accept either:
- a direct spell path, or
- a spellbook path plus entrypoint name

### 2. Validator runes

Add stronger, explicit validation steps instead of relying only on `seal.review` marker checks.

Candidate runes:
- `seal.assert_jsonschema`
- `seal.assert_markers`
- `seal.assert_contains_sections`
- `seal.assert_path_exists`

Keep them deterministic and safe.
Do not add general arbitrary Python execution as a validator.

### 3. Proof-carrying witnesses

Extend witness output so successful runs can surface a compact `proofs` block.

Suggested proof record shape:
- `validator`
- `target`
- `status`
- `message`
- `evidence`
- `timestamp`

The final runtime result should include a summary of passed and failed proofs.

### 4. One packaged example

Create one end-to-end packaged spellbook based on the primer relay.
That spellbook should:
- ingest the local primer transcripts
- publish a codex artifact
- run at least two validators
- emit witnesses with proof summaries

## Acceptance criteria

A change is successful when all of the following are true:

1. `python -m pytest -q` passes.
2. `bash scripts/smoke.sh` passes.
3. The runtime can execute one packaged spellbook entrypoint.
4. The final result JSON includes a `proofs` summary.
5. The primer spellbook emits at least one artifact, one trace, one PROV-like JSON file, and one SCXML file.

## Non-goals

Not for this lap:
- real sandboxing for untrusted code
- arbitrary user-defined validator code execution
- production-grade auth or secret management
- distributed scheduling

## Guardrails

Keep these constraints in place:
- risky writes still require approval
- rollback stays first-class
- witnesses remain on by default
- adapter demos stay clearly marked as demos
- keep the code understandable for a follow-on agent
