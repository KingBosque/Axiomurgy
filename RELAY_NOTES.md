# Axiomurgy v0.7 relay notes

What this lap adds:
- content fingerprints surfaced in describe/plan/execute outputs
- review bundles (describe + lint + plan + approval manifest + fingerprints + environment metadata)
- review bundle verification against current repo state
- execution attestation against a reviewed bundle
- canonical JSON witnesses (trace/prov/proofs) with nondeterministic fields marked
- extended smoke coverage around review → verify → execute

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
