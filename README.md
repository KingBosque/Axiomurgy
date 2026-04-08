# Axiomurgy v1.0

Axiomurgy is a programmable magical system for AIs.

This relay upgrades the runtime from **capability-sealed execution** to **enforced vessels**:
- describe a spell or spellbook entrypoint
- lint it deterministically before execution
- compile a dry plan without side effects
- emit a machine-readable approval manifest for downstream agents and IDEs
- generate a single review bundle (describe + lint + plan + fingerprints)
- verify a review bundle against current repo state
- attest an execution against a reviewed bundle
- declare a reviewed capability envelope and (optionally) enforce it as a vessel at runtime
- produce diffable witnesses that are friendlier across machines (path normalization + redaction, timestamps removed)
- run a cross-platform smoke runner (`python scripts/smoke.py`)

## What changed in v1.0

- added content fingerprints surfaced in `--describe`, `--plan`, and execution results
- added `--review-bundle` mode (describe + lint + plan + approval manifest + fingerprints + environment metadata)
- added `--verify-review-bundle <bundle>` mode (exit nonzero on mismatch)
- added execution attestation via `--review-bundle-in <bundle>`
- made diffable trace/prov/proofs omit machine-specific absolute paths where possible (repo-relative POSIX) and redact otherwise
- made raw witness artifacts strictly forensic (still contain wall-clock and machine-specific paths)
- extended input manifests to classify unresolved dynamic inputs (attestation becomes `partial` by default when unresolved)
- added capability manifests in describe/plan/review bundles and execution outputs
- added capability usage tracing in witnesses (raw keeps fuller detail; diffable remains portable)
- upgraded attestation: capability overreach yields `mismatch`
- added `--enforce-review-bundle` to block undeclared capability use before side effects
- added denial events and first-class execution outcomes in execution results and witnesses
- added `scripts/smoke.py` as the cross-platform smoke runner

## Core features

- JSON Schema spell validation
- JSON Schema spellbook validation
- dependency-aware execution planning
- deterministic linting for spells and spellbooks
- plan summaries, approval manifests, and review bundles
- policy checks and human approval gates
- rollback and compensation semantics
- PROV-like witness export and SCXML plan export
- proof-carrying witness summaries
- MCP stdio resource and tool integration
- OpenAPI-driven HTTP calls
- lightweight confidence and entropy tracking

## Repository map

- `axiomurgy.py` - reference runtime
- `spell.schema.json` - spell contract
- `spellbook.schema.json` - spellbook manifest contract
- `examples/` - direct runnable spells
- `spellbooks/primer_codex/` - packaged proof-carrying spellbook with preflight artifacts
- `primers/` - local copies of the seven uploaded primer transcripts
- `adapters/` - demo MCP and OpenAPI servers
- `policies/` - default runtime policy
- `artifacts/` - generated outputs and witnesses for direct examples
- `scripts/smoke.py` - end-to-end verification (cross-platform)
- `scripts/smoke.sh` - legacy bash smoke
- `tests/test_runtime.py` - regression checks
- `AGENTS.md`, `CURSOR_PROMPTS.md`, and `.cursor/rules/` - Cursor handoff

## Install

```bash
cd axiomurgy_v0_6
python -m pip install -r requirements.txt
```

## Quick start

Describe the packaged spellbook entrypoint:

```bash
python axiomurgy.py spellbooks/primer_codex --describe
```

Lint the packaged spellbook:

```bash
python axiomurgy.py spellbooks/primer_codex --lint
```

Compile a dry plan and write an approval manifest:

```bash
python axiomurgy.py spellbooks/primer_codex --plan \
  --manifest-out spellbooks/primer_codex/artifacts/primer_codex_publish_v0_6.approval_manifest.json
```

Generate a review bundle:

```bash
python axiomurgy.py spellbooks/primer_codex --review-bundle \
  > spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json
```

Verify a review bundle:

```bash
python axiomurgy.py spellbooks/primer_codex --verify-review-bundle \
  spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json
```

Run the direct primer relay:

```bash
python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish
```

Run the packaged spellbook entrypoint:

```bash
python axiomurgy.py spellbooks/primer_codex --approve publish
```

Run the packaged spellbook entrypoint *with attestation against a reviewed bundle*:

```bash
python axiomurgy.py spellbooks/primer_codex --approve publish \
  --review-bundle-in spellbooks/primer_codex/artifacts/primer_codex_publish_v0_7.review_bundle.json
```

## Ouroboros Chamber (v1.1, optional)

Ouroboros Chamber is an **opt-in, bounded cyclic runner** for supervised iterative improvement.
It does not replace normal execution; it runs only when `--cycle-config` is provided.

Example:

```bash
python axiomurgy.py examples/ouroboros_score_fixture.spell.json \
  --cycle-config path/to/cycle.json
```

Notes:
- review bundles + attestation still apply when `--review-bundle-in` is provided
- enforced vessels still apply when `--enforce-review-bundle` is provided
- mutations are restricted to explicit allowlists and applied to **shadow spell files** under the artifacts directory

Run the MCP relay:

```bash
python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage
```

Run the OpenAPI rollback demo:

```bash
python adapters/mock_issue_server.py >/tmp/axiomurgy_issue_server.log 2>&1 &
python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket
```

That final command is expected to return `status: failed`, because the spell intentionally triggers a post-write failure in order to exercise compensation.

## Describe and plan result shape

A describe run returns repository metadata without execution:

```json
{
  "mode": "describe",
  "kind": "spellbook",
  "spellbook": {
    "name": "primer_codex",
    "resolved_entrypoint": "publish_codex"
  }
}
```

A plan run returns ordered steps plus a manifest:

```json
{
  "mode": "plan",
  "required_approvals": [
    {
      "step_id": "publish",
      "rune": "gate.file_write",
      "effect": "write",
      "granted": false
    }
  ],
  "write_steps": [
    {
      "step_id": "publish",
      "target": "../artifacts/primer_codex_v0_6.md"
    }
  ],
  "manifest": {
    "policy_path": ".../policies/default.policy.json",
    "artifact_dir": ".../spellbooks/primer_codex/artifacts",
    "simulate_recommendation": true
  }
}
```

## Execution result shape

Successful runs include a `proofs` block:

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

Each executed run also emits:
- `*.trace.json`
- `*.prov.json`
- `*.scxml`
- `*.proofs.json`

## Validation

Fast tests:

```bash
python -m pytest -q
```

Full smoke (cross-platform):

```bash
python scripts/smoke.py
```

Legacy bash smoke (requires `bash`, e.g. Git Bash or WSL):

```bash
bash scripts/smoke.sh
```

## Cursor workflow

If you are using Cursor:
- start with `AGENTS.md`
- let project rules load from `.cursor/rules/`
- use `CURSOR_PROMPTS.md` for ready-to-paste tasks
- use `.cursor/environment.json` as the starter background-agent install config
- prefer `--describe`, `--lint`, and `--plan` before changing execution behavior

## Current project direction

The next relay is defined in `NEXT_LAP_SPEC.md`.
The short version: bind reviewed preflight manifests more tightly to execution through **fingerprints, review bundles, and diffable witnesses**.
