# Axiomurgy v0.2

Axiomurgy is a programmable magical system for AIs.

A spell is not a vibe. It is a **typed, permissioned, provenance-bearing workflow**.
Instead of mana, it spends scope, authority, exposure, latency, and uncertainty.
Instead of wands, it uses schemas, protocols, tools, policies, and witnesses.

## What v0.2 adds

Compared with the earlier kickoff draft, v0.2 adds real runtime machinery:

- Draft 2020-12 JSON Schema validation for spells
- dependency-aware execution planning (`requires` / `depends_on`)
- policy evaluation and human approval gates (`--approve step_id` / `--approve all`)
- rollback / compensation semantics for side effects (`step.compensate`)
- provenance export and raw execution traces
- SCXML plan export
- MCP stdio integration for resources and tools
- OpenAPI-driven HTTP calls (local mock server)

## Project structure

- `AXIOMURGY_SPEC.md` — design spec and metaphysics
- `spell.schema.json` — spell contract
- `axiomurgy.py` — runtime
- `requirements.txt` — Python dependencies
- `policies/default.policy.json` — reference approval policy
- `adapters/demo_mcp_server.py` — local MCP resource/tool server
- `adapters/mock_issue_server.py` — local HTTP server for OpenAPI demos
- `adapters/mock_issue_api.openapi.yaml` — OpenAPI surface for the issue server
- `examples/primer_to_axioms.spell.json` — reads local `primers/` and writes an artifact (with compensation)
- `examples/primer_via_mcp.spell.json` — reads primers via MCP, stages output via an MCP tool (with compensation)
- `examples/openapi_ticket_then_fail.spell.json` — creates a ticket then intentionally fails to prove rollback
- `artifacts/` — generated outputs, traces, provenance, and SCXML plans (gitignored)
- `axiomurgy_workspace/` — MCP demo workspace notes (gitignored)

## Quick start (Windows PowerShell)

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the direct-file primer relay:

```bash
python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish
```

Run the MCP version:

```bash
python axiomurgy.py examples/primer_via_mcp.spell.json --approve publish
```

Run the OpenAPI rollback demo:

```bash
# in one terminal
python adapters/mock_issue_server.py

# in another terminal
python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket
```

The last command is expected to **fail on purpose** after the external write.
The runtime should then issue the compensation action and delete the created ticket.
