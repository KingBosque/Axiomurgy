# Axiomurgy v1.0 relay notes

What this lap adds:
- diffable witnesses with path normalization (repo-relative POSIX) and redaction for opaque absolute paths
- raw witness artifacts preserved for forensics (wall-clock times + machine-specific paths)
- input manifests classify declared_static vs declared_dynamic vs unresolved_dynamic
- unresolved_dynamic inputs downgrade attestation to `partial` by default
- capability manifests and a reviewed capability envelope in review bundles
- capability usage tracing in execution witnesses (raw keeps fuller detail; diffable remains portable)
- attestation marks undeclared capability use as `mismatch`
- enforced vessels: `--enforce-review-bundle` blocks undeclared capability use before side effects
- denial events are recorded in raw + diffable traces (`capability_denials`)
- execution outcomes are first-class in results (`execution_outcome`)
- cross-platform smoke runner: `python scripts/smoke.py`
- Ouroboros Chamber (v1.1): optional cyclic runner (`--cycle-config`) with allowlisted mutations, deterministic scoring, rollback via shadow spells, and cycle witnesses
- Ouroboros Chamber (v1.2): selective recall, mutation families (`enum` / `numeric` / `string`), deterministic proposals with `proposal_id`, richer cycle witnesses (`recall`, per-revolution recall snapshots)
- Ouroboros Chamber (v1.3): optional deterministic preflight **proposal_plan** (review-aware envelope checks, fingerprint match, unresolved-input risk), `preflight_skips`, skip inadmissible proposals before veil without flux spend
- Ouroboros Chamber (v1.4): **effect signatures** per proposal (mechanical plan/locus/capability shape), deterministic **diversified ranking** within admissibility tiers, `mutation_families` supports `flag` and `path_choice`
- Ouroboros Chamber (v1.5): **score-channel integrity** — static comparison of `target_metric.path` to resolved `gate.file_write` destinations; **clear-break** disconnects are `inadmissible`; otherwise `uncertain`; optional `score_channel_sensitive_paths` / `block_score_channel_sensitive_mutations`
- Ouroboros Chamber (v1.6): optional **`acceptance_contract`** (guardrails, tie-break chain, `reject_if` vs last accept), deterministic seal evaluation, per-revolution **`seal_decision`**, witness **`acceptance_summary`**
- Ouroboros Chamber (v1.7): **`baseline_registry`**, **`promotion_records`**, **`lineage_summary`**, seal **`baseline_reference_used_id`**, per-revolution **`active_baseline_id`**; optional **`lineage_policy`**
- Ouroboros Chamber (v1.8): **`run_id`**, per-run **`artifact_root`** under **`ouroboros_runs/`**, **`run_manifest`**, witness **`run_capsule`**; optional **`run_capsule`** retention flags
- Ouroboros Chamber (v1.9): **`revolution_capsules`** (nested under **`run_capsule`**), per-revolution artifact roots **`revolutions/rev_NNNN/`** for executed veil attempts, **`proposal_id_to_revolution_id`**, revolution counts on witness / manifest / cycle result; preflight skips get capsule rows only; optional **`revolution_retention`** (default preserve all)
- Ouroboros Chamber (v2.0): **`replay_record.json`** per executed veil; **`--replay-revolution-dir`** / **`--replay-run-manifest`** + **`--replay-revolution-id`**; **`replay_summary`** artifacts; deterministic **`replay_status`**; no writes under source **`revolutions/`**
- Optional metaphysical reasoning (v2.1): minimal advisory **`reasoning`** when **`AXIOMURGY_REASONING=1`** (default off); **`AXIOMURGY_REASONING_EXPERIMENTAL=1`** adds **`reasoning.experimental`** (correspondence, friction, combinatorics, Wyrd hints, generation); narrow attestation allowlist per subtree; **`telos`** separate from Ouroboros **`acceptance_contract`**; **`AXIOMURGY_WYRD=1`** + SQLite for **`experimental.wyrd_hints`** when experimental is on

Verified demos in this relay:
- `python axiomurgy.py spellbooks/primer_codex --describe`
- `python axiomurgy.py spellbooks/primer_codex --lint`
- `python axiomurgy.py spellbooks/primer_codex --plan`
- `python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish`
- `python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage`
- `python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket`

Why this lap exists:
- packaged execution and proofs were useful, but downstream agents still lacked strong preflight visibility
- another IDE or agent should be able to inspect a spellbook before running it
- approvals and planned writes should be surfaced explicitly before side effects happen

Notable implementation choices:
- linting stays deterministic and local
- plan mode does not execute spell steps
- manifests summarize risk, approvals, writes, and external calls in a machine-readable form
- execution semantics, rollback, and witnesses remain intact from earlier laps

Suggested next relay:
- stricter “reviewed execution required” flag for write-capable runs
- structured diff tooling over manifests and witnesses
