# Axiomurgy v0.6

Axiomurgy is a programmable magical system for AIs.

This relay upgrades the runtime from **packaged execution with proofs** to a stronger **preflight workflow**:
- describe a spell or spellbook entrypoint
- lint it deterministically before execution
- compile a dry plan without side effects
- emit a machine-readable approval manifest for downstream agents and IDEs

## What changed in v0.6

- added `--describe` mode for resolved spell and spellbook entrypoints
- added `--plan` mode for deterministic preflight execution summaries
- added `--lint` mode for local spell and spellbook checks
- added approval manifests that surface:
  - required approvals
  - planned writes
  - external calls
  - policy path
  - artifact directory
  - simulation recommendation
- expanded tests and smoke coverage to verify describe → lint → plan → execute on the packaged spellbook
- refreshed the Cursor handoff so agents can inspect the repo before they act

## Core features

- JSON Schema spell validation
- JSON Schema spellbook validation
- dependency-aware execution planning
- deterministic linting for spells and spellbooks
- plan summaries and approval manifests
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
- `scripts/smoke.sh` - end-to-end verification
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

Run the direct primer relay:

```bash
python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish
```

Run the packaged spellbook entrypoint:

```bash
python axiomurgy.py spellbooks/primer_codex --approve publish
```

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

Full smoke:

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
