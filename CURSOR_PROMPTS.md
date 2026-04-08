# Cursor prompts for Axiomurgy

## 1. Ask mode: map the repo first

```text
Read AGENTS.md, README.md, RELAY_NOTES.md, and NEXT_LAP_SPEC.md. Then inspect axiomurgy.py, spell.schema.json, spellbook.schema.json, examples/, spellbooks/, policies/, adapters/, tests/, and scripts/. Give me a concise map of the runtime architecture, the spellbook loading flow, the preflight flow (describe, lint, and plan), and the main risks if we implement v0.7 next.
```

## 2. Agent mode: verify the relay package

```text
Use AGENTS.md as the project contract. Verify that this repo is runnable end to end. Install dependencies from requirements.txt, run python -m pytest -q, then run bash scripts/smoke.sh. If anything fails, patch the smallest possible set of files to make the relay pass. Summarize what you changed and any remaining risks.
```

## 3. Agent mode: implement the next milestone

```text
Use AGENTS.md and NEXT_LAP_SPEC.md as the source of truth. Implement Axiomurgy v0.7 focused on fingerprints, review bundles, and diffable witnesses. Work incrementally: first add stable fingerprint helpers, then add review bundle generation, then add execution-time verification, then add structured diff mode, then update tests and smoke coverage. Keep approval, rollback, and witness defaults intact. Run pytest and the smoke script before finishing.
```

## 4. Agent mode: stay preflight-first

```text
Before changing runtime execution, run describe, lint, and plan on spellbooks/primer_codex. Use the approval manifest as your preflight contract. Do not remove policy gates, rollback semantics, or proof-carrying witnesses.
```

## 5. Background agent prompt

```text
This repo already contains .cursor/environment.json, AGENTS.md, tests, and a smoke script. Use those as your working contract. First make the current relay green, then implement the spec in NEXT_LAP_SPEC.md with small patches and clear summaries. Do not remove policy gates, rollback semantics, or proof-carrying witnesses.
```
