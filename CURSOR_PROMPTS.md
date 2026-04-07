# Cursor prompts for Axiomurgy

## 1. Ask mode: map the repo first

```text
Read AGENTS.md, README.md, RELAY_NOTES.md, and NEXT_LAP_SPEC.md. Then inspect axiomurgy.py, spell.schema.json, examples/, policies/, adapters/, tests/, and scripts/. Give me a concise map of the runtime architecture, the current invariants, the validation workflow, and the most likely risks if we implement v0.5 next.
```

## 2. Agent mode: verify the relay package

```text
Use AGENTS.md as the project contract. Verify that this repo is runnable end to end. Install dependencies from requirements.txt, run python -m pytest -q, then run bash scripts/smoke.sh. If anything fails, patch the smallest possible set of files to make the relay pass. Summarize what you changed and any remaining risks.
```

## 3. Agent mode: implement the next milestone

```text
Use AGENTS.md and NEXT_LAP_SPEC.md as the source of truth. Implement Axiomurgy v0.5 focused on spellbooks and proof-carrying witnesses. Work incrementally: first add the spellbook manifest and runtime loading path, then add deterministic validator runes, then add proof summaries to witness output, then add one packaged primer spellbook and tests. Keep approval, rollback, and witness defaults intact. Run pytest and the smoke script before finishing.
```

## 4. Background agent prompt

```text
This repo already contains .cursor/environment.json, AGENTS.md, tests, and a smoke script. Use those as your working contract. First make the current relay green, then implement the spec in NEXT_LAP_SPEC.md with small commits and clear summaries. Do not remove policy gates, rollback semantics, or witness exports.
```
