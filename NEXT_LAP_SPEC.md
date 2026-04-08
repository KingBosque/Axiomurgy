# Axiomurgy v1.1 target

**Status:** Ouroboros v1.2 (selective recall + mutation families) is implemented in the reference runtime; see `README.md` and `examples/cycles/ouroboros_cycle_v12.json` for the current shape.

## Working title

**Ouroboros Chamber (optional cyclic runner)**

## Why this should be next

Axiomurgy now has:
- packaged spellbooks
- deterministic validators
- proofs and witnesses
- describe / lint / plan preflight modes
- approval manifests

What it still lacks is a strong **link between review and execution**.
Right now an agent can inspect a plan and manifest before running a spell, but the runtime does not yet make it easy to prove that:
- the reviewed manifest still matches the spell being executed
- the spellbook did not change after approval
- two witness trails differ only in expected ways

That is the gap for v0.7.

## Scope

### 1. Stable fingerprints

Add deterministic hashes for:
- spells
- spellbooks
- compiled plans
- approval manifests

The goal is that two agents can independently compute the same fingerprint for the same preflight state.

Suggested outputs:
- `spell_fingerprint`
- `spellbook_fingerprint`
- `plan_fingerprint`
- `manifest_fingerprint`

### 2. Review bundles

Add a machine-readable bundle that captures:
- target path
- resolved entrypoint
- policy path
- artifact dir
- plan fingerprint
- manifest fingerprint
- granted approvals
- creation timestamp

Suggested CLI shape:

```text
python axiomurgy.py spellbooks/primer_codex --review-bundle \
  > spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json

python axiomurgy.py spellbooks/primer_codex --verify-review-bundle \
  spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json
```

### 3. Execute against a reviewed bundle

Allow execution to consume a review bundle and verify that:
- the spellbook still resolves the same way
- the current plan fingerprint matches the reviewed one
- the current manifest fingerprint matches the reviewed one
- the requested approvals are consistent with the reviewed grant set

Suggested CLI shape:

```text
python axiomurgy.py spellbooks/primer_codex --approve publish \
  --review-bundle-in spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json
```

If verification fails, execution should stop before side effects (or at minimum surface `attestation.status: mismatch`).

### 4. Diffable witnesses

Add a comparison mode for:
- two approval manifests
- two witness traces
- two proof summaries

Keep the witnesses deterministic and structured for agent consumption (canonical JSON, nondeterminism explicitly marked).

## Acceptance criteria

A change is successful when all of the following are true:

1. `python -m pytest -q` passes.
2. `bash scripts/smoke.sh` passes.
3. Plan mode emits stable fingerprints.
4. A review bundle can be generated and then verified during execution.
5. Diff mode clearly reports changes in approvals, writes, or witness outcomes.

## Scope (v0.8)

### 1. Portable diffable witnesses

Diffable `*.trace.json` / `*.prov.json` / `*.proofs.json` should normalize paths to repo-relative POSIX where possible, and redact otherwise. Raw `*.raw.json` artifacts remain forensic.

### 2. Unresolved dynamic input honesty

Review bundles and attestation should surface when dynamic inputs are not fully captured at preflight (`unresolved_dynamic`), and downgrade attestation to `partial` by default.

### 3. Cross-platform smoke

Add `python scripts/smoke.py` as the primary local relay check.

## Next (v0.10 sketch)

- stricter “reviewed execution required” flag for write-capable runs
- structured diff tooling over manifests and witnesses

## Non-goals

Not for this lap:
- real cryptographic signing infrastructure
- external key management
- distributed approvals
- production secret storage

## Guardrails

Keep these constraints in place:
- risky writes still require approval
- rollback stays first-class
- proofs remain on by default when witness recording is enabled
- lint and plan remain deterministic and local
- adapters stay clearly marked as demos
- keep the code understandable for a follow-on agent
