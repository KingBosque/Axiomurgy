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
