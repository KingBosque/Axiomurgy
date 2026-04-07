# Axiomurgy v0.1

Axiomurgy is a programmable "magic system" for AIs.

It treats every spell as a **typed, permissioned, provenance-bearing workflow**.
Instead of mana, it consumes budget, authority, uncertainty tolerance, and time.
Instead of wands, it uses schemas, tools, memory, policies, and sandboxes.

## Core idea

A spell succeeds only when these five things align:

1. **Name** — the target is represented in a machine-usable way.
2. **Intent** — the desired transformation is explicit.
3. **Authority** — the caster has the capability to do it.
4. **Witness** — the system can explain and trace what happened.
5. **Vessel** — execution happens in a bounded runtime.

## Project files

- `AXIOMURGY_SPEC.md` — worldbuilding and systems design document.
- `spell.schema.json` — a JSON Schema for spells.
- `requirements.txt` — Python dependency (`jsonschema` for Draft 2020-12 validation).
- `axiomurgy.py` — a small reference runtime.
- `examples/research_brief.spell.json` — example spell.
- `examples/inbox_triage.spell.json` — example spell.

## Quick start

```bash
pip install -r requirements.txt
python axiomurgy.py examples/research_brief.spell.json
python axiomurgy.py examples/inbox_triage.spell.json
```

The reference runtime is intentionally minimal. It demonstrates:

- spell parsing
- contract validation
- capability checks
- step execution
- provenance logging
- reversible / approval-aware design hooks

## Status

This is a kickoff artifact, not a finished platform.
The next implementation steps are:

- compile spells to a workflow/state machine
- add tool adapters for real systems
- add policy engines, quorum approval, and rollback actions
- add provenance export formats
