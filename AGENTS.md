# Axiomurgy agent guide

You are working inside **Axiomurgy**, a programmable magical system for AIs.

Start here, in order:
1. Read `README.md`.
2. Read `RELAY_NOTES.md`.
3. Read `NEXT_LAP_SPEC.md`.
4. Skim `axiomurgy.py`, `axiomurgy/bundled/` (canonical schemas/policy), repo-root `spell.schema.json` / `spellbook.schema.json` (mirrors), `examples/`, `spellbooks/`, `policies/`, `adapters/`, `tests/`, and `scripts/`.
5. Optional: `packages/` TypeScript workspace (`npm install` / `npm test`) for seam contracts and Vermyth HTTP tooling; Python remains authoritative for execution.

For a working tree, install dependencies with `python -m pip install -e ".[dev]"` from the repository root (see `README.md` and `docs/CLI_CONTRACTS.md`).

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

This v1.0 relay adds:
- portable review contracts (diffable witnesses with path normalization + redaction)
- unresolved dynamic input signaling in review bundles and attestation
- a cross-platform smoke runner (`python scripts/smoke.py`)
- content fingerprints surfaced in describe/plan/execute outputs
- review bundles (describe + lint + plan + manifest + fingerprints + environment metadata)
- review bundle verification against current repo state
- execution attestation against a reviewed bundle
- capability manifests and reviewed capability envelopes for capability-sealed execution
- capability usage tracing in witnesses and overreach mismatch in attestation
- enforced vessels: `--enforce-review-bundle` can block undeclared capability use before side effects
- machine-readable capability denial events in witness traces
- execution outcomes separate from attestation semantics

This v1.1 relay adds:
- Ouroboros Chamber: an opt-in cyclic runner (`--cycle-config`) for bounded supervised improvement
- deterministic scoring and rollback via shadow spell files (no repo mutation by default)
- cycle witnesses (`*.ouroboros.json` + `*.ouroboros.raw.json`) for auditability
- canonical JSON witnesses (trace/prov/proofs) with nondeterministic fields marked

This v1.2 relay adds:
- Ouroboros recall summaries (bounded recent successes/failures) and top-level `recall` on cycle witnesses
- mutation families (`enum`, `numeric`, `string`) with stable proposal ordering and `proposal_id`; legacy `mutation_targets` still supported (not combinable with `mutation_families` in one config)
- optional `reject_on_noop`, `tie_break`, and per-revolution `score_before` / `score_after` / `accept_reject_reason`

This v1.3 relay adds:
- Ouroboros **proposal_plan** artifacts (`*.proposal_plan.json` / `*.proposal_plan.raw.json`): deterministic, review-aware preflight classification of proposals against optional reviewed capability envelopes and spell fingerprints
- **preflight_skips** on cycle witnesses for proposals skipped before execution (clear envelope overreach only—otherwise `uncertain`)
- stable ranking: admissible, then uncertain, then inadmissible; inadmissible proposals do not consume `flux_budget` / veil attempts

This v1.4 relay adds:
- **Effect-signature diversification** for Ouroboros: within each admissibility tier, proposals are ordered in deterministic round-robin by canonical effect signature (novel signatures before near-duplicate candidates at the same locus)
- `proposal_plan` fields: `effect_signature`, `effect_signature_id`, `signature_rank`, `duplicate_of_signature`, `diversification_summary`; `proposal_plan_version` `1.4.0`
- bounded mutation families `flag` and `path_choice` in cycle configs (allowlisted targets only)

This v1.5 relay adds:
- **Score-channel integrity** for Ouroboros `fixture_score`: mechanical comparison of the metric file path to resolved `gate.file_write` targets; **inadmissible** only on a **clear break** (baseline has a single aligned writer to the metric file; proposal has none; paths resolved); otherwise **uncertain**
- `proposal_plan_version` `1.5.0`; `score_channel_contract`, `score_channel_summary`, per-proposal score-channel fields; cycle witnesses include score-channel blocks; optional `score_channel_sensitive_paths` and `block_score_channel_sensitive_mutations` on cycle configs

This v1.6 relay adds:
- Optional **`acceptance_contract`** on cycle configs: primary metric mode (`maximize` / `minimize`), `required_improvement` (defaults merge with legacy `stop_conditions.min_improvement` when the block is absent), **guardrails** on secondary fixture metrics, **`tie_breakers`**, and mechanical **`reject_if`** flags versus the last accepted proposal
- Deterministic **`evaluate_acceptance_contract`** seal step (per-revolution **`seal_decision`** JSON), resolved **`acceptance_contract`** and **`acceptance_summary`** counters on cycle witnesses; backward-compatible defaults when `acceptance_contract` is omitted

This v1.7 relay adds:
- **Baseline lineage** for Ouroboros: deterministic **`baseline_id`** / **`parent_baseline_id`** registry, optional **`rejected_candidate_snapshot`** rows (when enabled), **`promotion_records`** on accept only, **`baseline_reference_used_id`** on seal output, **`active_baseline_id`** per revolution, **`lineage_summary`** on the witness and cycle result; optional **`lineage_policy`** object on cycle configs for future non-breaking hooks

This v1.8 relay adds:
- **Run capsules** for Ouroboros: per-invocation **`run_id`** and isolated **`artifact_root`** under **`ouroboros_runs/`**, raw+diff **`run_manifest`**, witness **`run_capsule`** + **`key_artifact_paths_relative`**; cycle result includes **`run_artifact_root`**, **`run_manifest_path`**; optional **`run_capsule`** settings (`enabled`, `keep_last_n_runs`, `prune_old_capsules`)

This v1.9 relay adds:
- **Revolution capsules** for Ouroboros: deterministic **`revolution_id`** per preflight skip or veil attempt; executed revolutions use **`<run_root>/revolutions/rev_NNNN/`** for witnesses and **`shadow.spell.json`** copy; skipped revolutions record **`executed: false`** without fake execution artifacts; witness + **`run_manifest`** + cycle result expose **`revolution_capsules`**, **`proposal_id_to_revolution_id`**, counts, and **`revolution_artifact_roots`**; optional **`revolution_retention`** (default **preserve all**)

This v2.0 relay adds:
- **Replay** for executed Ouroboros revolutions: **`replay_record.json`** per veil; CLI **`--replay-revolution-dir`** or **`--replay-run-manifest`** + **`--replay-revolution-id`** with optional **`--replay-artifact-dir`**; mechanical **`replay_status`** (`match` / `drift` / `non_replayable`) comparing score, seal, execution fingerprint slice, and optional attestation; isolated replay witnesses; spellbook targets **`non_replayable`**

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
