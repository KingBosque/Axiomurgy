# Axiomurgy v0.3

Axiomurgy is a programmable magical system for AIs.

This pass makes the project concrete rather than merely descriptive:

- JSON Schema spell validation
- dependency-aware execution planning
- policy checks and human approval gates
- rollback and compensation semantics
- PROV-like witness export and SCXML plan export
- MCP stdio resource and tool integration
- OpenAPI-driven HTTP calls
- lightweight confidence and entropy tracking

## Project layout

- [`axiomurgy.py`](axiomurgy.py) — runtime (CLI)
- [`spell.schema.json`](spell.schema.json) — spell contract
- [`requirements.txt`](requirements.txt) — Python dependencies
- [`policies/default.policy.json`](policies/default.policy.json) — reference policy
- [`adapters/`](adapters/) — MCP demo server and OpenAPI mock issue server
- [`examples/`](examples/) — v0.3 demos; [`examples/research_brief.spell.json`](examples/research_brief.spell.json) and [`examples/inbox_triage.spell.json`](examples/inbox_triage.spell.json) are smaller smoke examples compatible with this schema
- [`primers/`](primers/) — local text inputs for primer relays
- [`artifacts/`](artifacts/) — generated traces, PROV JSON, SCXML (gitignored)
- [`RELAY_NOTES.md`](RELAY_NOTES.md) — relay changelog and verification notes

## Quick start (Windows PowerShell)

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the direct-file primer relay (approves the write step `publish`):

```bash
python axiomurgy.py examples/primer_to_axioms.spell.json --approve publish
```

Run the MCP relay (approves the write step `stage`):

```bash
python axiomurgy.py examples/primer_via_mcp.spell.json --approve stage
```

Run the OpenAPI rollback demo:

```bash
# Terminal A: start the mock issue API
python adapters/mock_issue_server.py

# Terminal B: expect failure after the write, then compensation deletes the ticket
python axiomurgy.py examples/openapi_ticket_then_fail.spell.json --approve create_ticket
```

That final command is expected to **fail intentionally** after the write and then **compensate** (delete) the created ticket.

Optional flags:

- `--policy path\to\policy.json` (defaults to `policies/default.policy.json`)
- `--artifact-dir path\to\artifacts` (defaults to `artifacts`)
- `--simulate` to suppress real external writes where supported

## Smaller examples

Policy may require explicit approvals for writes at medium risk or above. For a quick run:

```bash
python axiomurgy.py examples/research_brief.spell.json --approve stage
python axiomurgy.py examples/inbox_triage.spell.json --approve approval --approve archive
```
