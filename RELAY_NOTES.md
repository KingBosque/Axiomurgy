# Axiomurgy v0.6 relay notes

What this lap adds:
- `--describe` mode for resolved spell and spellbook entrypoints
- `--lint` mode for deterministic local validation
- `--plan` mode for dry execution summaries without side effects
- approval manifests for downstream agents and IDEs
- stronger smoke coverage around the full preflight chain
- refreshed Cursor handoff docs and rules for preflight-first work

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
- stable fingerprints for spells, spellbooks, plans, and manifests
- review bundles that can be approved once and verified at execution time
- diff tooling for manifests and witness trails
