# Axiomurgy v0.6 target

## Working title

**Plan mode, linting, and approval manifests**

## Why this should be next

Axiomurgy now has:
- direct spells
- packaged spellbooks
- deterministic validators
- proof-carrying witnesses

What it still lacks is a strong **preflight story**.
Right now a spell can run and produce good witnesses after the fact, but the runtime still makes it too hard to answer simple questions *before* execution:
- what entrypoints does this spellbook expose?
- what writes are about to happen?
- which steps require approval?
- are there packaging or dependency problems before I run anything?

That is the gap for v0.6.

## Scope

### 1. Plan mode

Add a runtime mode that resolves a spell or spellbook and prints a deterministic execution summary without running side effects.

Suggested CLI forms:

```text
python axiomurgy.py spellbooks/primer_codex --describe
python axiomurgy.py spellbooks/primer_codex --entrypoint publish_codex --plan
```

Plan output should include:
- spell name
- spellbook name and entrypoint when relevant
- ordered steps
- step effects and runes
- referenced dependencies
- planned writes
- steps that require approval under the active policy

### 2. Deterministic linting

Add a lint mode for spells and spellbooks.

Candidate checks:
- unknown rune names
- missing dependency references
- duplicate step ids
- output schema path not found
- spellbook entrypoint path not found
- spellbook default policy path not found
- dangerous write steps without any approval requirement in spell constraints or policy

Keep the linter deterministic and local.
Do not add arbitrary code execution.

### 3. Approval manifest

Add a machine-readable preflight file that summarizes approvals and risky effects.

Suggested shape:
- `required_approvals`
- `write_steps`
- `external_calls`
- `policy_path`
- `artifact_dir`
- `simulate_recommendation`

The idea is that another agent or IDE can inspect the manifest before deciding whether to proceed.

### 4. One plan-aware packaged example

Extend `spellbooks/primer_codex/` so it can be:
- described
- linted
- planned
- executed

Acceptance should demonstrate that the same packaged example works across all four modes.

## Acceptance criteria

A change is successful when all of the following are true:

1. `python -m pytest -q` passes.
2. `bash scripts/smoke.sh` passes.
3. The runtime can describe and plan at least one packaged spellbook entrypoint.
4. The linter reports success on the packaged primer spellbook.
5. The plan output or manifest clearly lists required approvals and planned writes.

## Non-goals

Not for this lap:
- real sandboxing for untrusted code
- distributed execution
- production auth or secret management
- arbitrary user-defined lint plugins

## Guardrails

Keep these constraints in place:
- risky writes still require approval
- rollback stays first-class
- proofs remain on by default when witness recording is enabled
- adapters stay clearly marked as demos
- keep the code understandable for a follow-on agent
