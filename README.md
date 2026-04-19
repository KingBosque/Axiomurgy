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

- `pyproject.toml` - package metadata and bundled data (schemas ship under `axiomurgy/bundled/`)
- `axiomurgy.py` - compatibility CLI shim
- `axiomurgy/bundled/` - **canonical** spell/spellbook schemas and default policy (see [docs/CONTRACT_FILES.md](docs/CONTRACT_FILES.md))
- `spell.schema.json` - repo-root mirror of bundled spell schema (refresh with `python scripts/sync_contract_mirrors.py`)
- `spellbook.schema.json` - repo-root mirror of bundled spellbook schema
- `examples/` - direct runnable spells
- `spellbooks/primer_codex/` - packaged proof-carrying spellbook with preflight artifacts
- `primers/` - local copies of the seven uploaded primer transcripts
- `adapters/` - demo MCP and OpenAPI servers
- `policies/` - default runtime policy
- `artifacts/` - generated outputs and witnesses for direct examples
- `scripts/smoke.py` - end-to-end verification (cross-platform)
- `scripts/smoke.sh` - legacy bash smoke
- `tests/test_runtime.py` - regression checks
- `packages/` - optional TypeScript workspace (JSON Schema checks, Vermyth HTTP shim, semantic-seam CLIs); Python remains the reference runtime
- `AGENTS.md`, `CURSOR_PROMPTS.md`, and `.cursor/rules/` - Cursor handoff

## Install

From a checkout of this repository:

```bash
python -m pip install -e ".[dev]"
```

Runtime dependencies only:

```bash
python -m pip install -r requirements.txt
```

### TypeScript seam (optional)

The repo includes an npm workspace under `packages/` for JSON Schema validation, a Vermyth HTTP client, and semantic-seam helpers. **Python remains the reference runtime** for execution, planning authority, and bundled contracts.

- **Canonical schemas for TS validation** are the same bundled files the Python runtime loads: `axiomurgy/bundled/spell.schema.json` and `spellbook.schema.json` (repo-root copies are mirrors; refresh with `python scripts/sync_contract_mirrors.py`).
- **Lockfile:** run `npm install` at the repo root and **commit `package-lock.json`** when present so CI can use `npm ci` for reproducible installs.
- **Root scripts:** `npm run build` (all workspaces), `npm test`, `npm run clean` (remove `packages/*/dist`), `npm run test:parity` (semantic recommend payload vs `docs/fixtures/ts-parity/`), `npm run test:ts:vermyth-smoke` (opt-in live HTTP; see below).
- **CLIs** (`semantic-seam-status`, `eval-semantic-recommendations`) are emitted under `packages/semantic-seam/dist/cli/` after `npm run build`. For development without a build, use `npm run dev:semantic-seam-status` / `npm run dev:eval-semantic-recommendations` from the `packages/semantic-seam` workspace (runs `tsx` on sources).
- **Parity fixtures:** regenerate with `python scripts/dump_ts_parity_fixtures.py` when the Python/Vermyth seam changes; `npm run test:parity` must match.

**Opt-in live Vermyth smoke (TypeScript):**

```bash
set AXIOMURGY_TS_VERMYTH_SMOKE=1
set AXIOMURGY_VERMYTH_BASE_URL=http://127.0.0.1:7777
npm run test:ts:vermyth-smoke
```

(Unix: `export AXIOMURGY_TS_VERMYTH_SMOKE=1` …)

Developer installs (tests) also:

```bash
python -m pip install -r requirements-dev.txt
```

CLI exit codes, stdout/stderr behavior, and entrypoints are summarized in [docs/CLI_CONTRACTS.md](docs/CLI_CONTRACTS.md). Canonical contract JSON locations and mirror sync are [docs/CONTRACT_FILES.md](docs/CONTRACT_FILES.md).

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

## Optional metaphysical reasoning (v2.1)

When **`AXIOMURGY_REASONING=1`**, `--describe` and `--plan` may include an advisory **`reasoning`** object (governor projection, **`telos.final_cause` / `telos.objectives`**, dialectic shell, scene, habitus, correspondence, friction, combinatorics search, Wyrd hints, generation candidates). Default is off so fingerprints and attestation behavior are unchanged. Reasoning paths are **allowlisted** in review-bundle compare (not required for attestation). **`AXIOMURGY_WYRD=1`** reads optional **`wyrd_hints`** from `<artifact-dir>/wyrd/graph.sqlite` when reasoning is enabled.

## Ouroboros Chamber (v1.8–v2.0, optional)

Ouroboros Chamber is an **opt-in, bounded cyclic runner** for supervised iterative improvement.
It does not replace normal execution; it runs only when `--cycle-config` is provided.

Example:

```bash
python axiomurgy.py examples/ouroboros_score_fixture.spell.json \
  --cycle-config path/to/cycle.json
```

v1.2 adds selective **recall** snapshots (bounded recent successes/failures), explicit **mutation families** (`enum`, `numeric`, `string`, `flag`, `path_choice`) with deterministic **proposal_id** ordering, optional **reject_on_noop**, and richer cycle witnesses. Legacy v1.1 configs using `mutation_targets` / `choices` remain valid; do not mix `mutation_families` and `mutation_targets` in one file.

**v1.3** adds deterministic **preflight proposal planning**: before revolutions, the runtime classifies each proposal as `admissible`, `uncertain`, or clearly `inadmissible` against an optional reviewed capability envelope (no guessing—only provable envelope overreach is inadmissible). It emits `*.proposal_plan.json` and `*.proposal_plan.raw.json`, skips inadmissible proposals **before** veil execution (without consuming flux budget), and records skips in `preflight_skips` on the cycle witness.

**v1.4** keeps that admissibility ordering, then **diversifies** within each tier using a stable mechanical **effect signature** (plan shape, predicted capabilities, mutation locus—not scalar candidate values). `proposal_plan` records include `effect_signature`, `effect_signature_id`, `signature_rank`, `duplicate_of_signature`, and a `diversification_summary`; the chamber consumes the same `ranked_proposals` list in diversified order.

**v1.5** adds deterministic **score-channel integrity** for `fixture_score`: it compares the metric file path (`target_metric.path`) to statically resolved `gate.file_write` targets. Proposals that **clearly** disconnect writes from the metric file are `inadmissible` before execution; ambiguous cases stay `uncertain`. `proposal_plan` includes `score_channel_contract`, per-proposal score-channel fields, and `score_channel_summary`; cycle witnesses echo the contract and summary. Optional cycle keys `score_channel_sensitive_paths` and `block_score_channel_sensitive_mutations` allow explicit operator bans on mutating named paths.

**v1.6** adds an optional **`acceptance_contract`** on cycle configs: explicit primary metric direction, `required_improvement` (defaults align with legacy `min_improvement` when the block is omitted), **guardrails** on additional fixture-score paths, ordered **tie-breakers**, and mechanical **`reject_if`** checks versus the last accepted proposal. Acceptance is decided by a deterministic **seal** evaluator; each revolution records a **`seal_decision`**, and witnesses include the resolved contract plus an **`acceptance_summary`** (accept/reject counters).

**v1.7** adds deterministic **baseline lineage**: a **`baseline_registry`** (machine-readable baseline records with parent links, logical ordering, metric and guardrail snapshots, admissibility/score-channel snapshots, and status), **`promotion_records`** on each accept (from/to baseline ids, proposal id, mechanical promotion reason, seal summary, metrics and guardrails before/after), **`baseline_reference_used_id`** on each **`seal_decision`** (concrete ids for primary and guardrail references), per-revolution **`active_baseline_id`**, and top-level **`lineage_summary`** counts. Optional **`lineage_policy`** (e.g. `record_rejected_snapshots`) is reserved for future contract hooks without changing seal math.

**v1.8** adds **run capsules**: each cycle invocation allocates a deterministic **`run_id`** (`run_NNNNNN` under `<artifact-dir>/ouroboros_runs/`) and writes all Ouroboros outputs (metrics, proposal plans, shadow spells, witnesses, **`run_manifest`**) under that directory so repeated runs do not overwrite each other. Witnesses include **`run_capsule`** metadata (fingerprints, `artifact_root`) and **`key_artifact_paths_relative`**. Cycle JSON may set **`run_capsule.enabled: false`** for legacy flat layout under the base artifact dir; optional **`keep_last_n_runs`** / **`prune_old_capsules`** control safe retention (off by default).

**v1.9** adds **revolution capsules** nested under the run capsule: each preflight skip or veil attempt gets a deterministic **`revolution_id`** (`rev_NNNN`). Executed revolutions write trace / prov / proofs / shadow copies under **`<run_root>/revolutions/rev_NNNN/`** so multiple revolutions in a single run do not overwrite each other (scoring stays on the run root via absolute `score_path`). Preflight-only skips get a lightweight capsule row (**`executed: false`**, **`skipped_reason`**) with **no** revolution artifact tree. Witnesses, **`run_manifest`**, and cycle results include **`revolution_capsules`**, **`proposal_id_to_revolution_id`**, **`revolution_count_*`**, and **`revolution_artifact_roots`**. Optional **`run_capsule.revolution_retention`** defaults to **`preserve_all`** for future pruning hooks (no deletion by default).

**v2.0** adds **replayable revolutions**: each executed veil writes **`replay_record.json`** under **`revolutions/rev_NNNN/`** (seal inputs, recorded score/seal/execution/attestation fingerprints). **`--replay-revolution-dir`** or **`--replay-run-manifest`** + **`--replay-revolution-id`** (diff manifest auto-loads the sibling **`.run_manifest.raw.json`** when paths are redacted) re-executes the stored **`shadow.spell.json`** under an isolated **`--replay-artifact-dir`**, emits **`replay_summary.json`** / **`.raw.json`**, and prints **`replay_status`**: **`match`**, **`drift`**, or **`non_replayable`** (e.g. missing record, fingerprint/policy mismatch, or attestation replay without a matching **`--review-bundle-in`**). Replay never writes into the source run tree.

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
