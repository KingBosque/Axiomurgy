# Axiomurgy v0.3 relay notes

What this lap adds:

- a real runtime with JSON Schema validation
- dependency-aware planning that preserves intended order while respecting references
- policy checks and explicit approval handling
- compensation on failed write workflows
- witness exports: trace JSON, PROV-like JSON, and SCXML
- MCP resource reads and tool calls
- OpenAPI contract execution with response validation
- confidence and entropy tracking per step

Verified demos:

- `examples/primer_to_axioms.spell.json` writes `artifacts/primer_to_axioms_v0_3.md`
- `examples/primer_via_mcp.spell.json` stages `axiomurgy_workspace/relay/primer_via_mcp_v0_3.md`
- `examples/openapi_ticket_then_fail.spell.json` fails intentionally and compensates the created ticket

Suggested next relay:

- proof-carrying spells
- richer policy VM
- parallel branches in the planner
- stronger semantic validators
- a first-class spellbook / package format
