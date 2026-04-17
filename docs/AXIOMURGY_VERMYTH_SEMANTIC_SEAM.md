# Axiomurgy ↔ Vermyth semantic seam

Axiomurgy integrates with Vermyth **only over HTTP** ([`axiomurgy/adapters/vermyth_http.py`](../axiomurgy/adapters/vermyth_http.py)): no `vermyth` package imports in the runtime path. Optional enrichment (recommendations, compile preview, gate) is **advisory** or parallel validation; Vermyth does not drive Axiomurgy planning or execution. (When both repos live under a common parent such as `ARCANE/`, Vermyth sources are alongside the Axiomurgy checkout.)

## Spell → `/arcane/recommend` input

Plan-time [`fetch_semantic_recommendations`](../axiomurgy/vermyth_integration.py) posts a task-shaped JSON body:

- `skill_id`: defaults to **`decide`**, matching `target_skills` on bundled **decide** manifests in Vermyth (`vermyth.arcane.recommend`).
- `input`: a dict with nested **`intent`** (`objective`, `scope`, `reversibility`, `side_effect_tolerance`).

Intent fields come from [`spell_level_vermyth_intent`](../axiomurgy/vermyth_export.py):

| Field | Derivation |
|-------|------------|
| `objective` | First 500 chars of `name` + `intent` + `constraints.risk`, newline-joined (stable fingerprint for hashing / display). |
| `scope` | `axiomurgy:{spell.name}` (truncated to 200 chars). |
| `side_effect_tolerance` | `HIGH` if `risk` in `high` / `critical`, else `MEDIUM`. |
| `reversibility` | If the compiled plan includes any **write** step: `PARTIAL` for low/medium risk, else `IRREVERSIBLE`. If no write step: `REVERSIBLE`. |

This matches the **risk / write** logic used per step in [`_intent_payload`](../axiomurgy/vermyth_export.py), aggregated once at spell level for a single probe. The recommend path does **not** send top-level `aspects` or `thresholds`; manifest tiers that rely only on `intent_subset_eq` and objective length match this shape.

## Spell → `vermyth_gate` (`POST /tools/decide`)

[`_decide_payload`](../axiomurgy/vermyth_integration.py) uses the **same** `intent` object as `spell_level_vermyth_intent`. It still adds placeholder **`aspects`** (`MOTION`, `FORM`, `VOID`) and a summary **`effects`** list for the gate until a richer derivation is justified. Gate behavior remains policy-gated and optional.

## Manifest matching

- Vermyth semantic bundles under `Vermyth/vermyth/data/arcane/bundles/` ship `recommendation.target_skills`. For Axiomurgy plan probes, **`skill_id` must appear in that list** (hence default `decide`).
- Bundles that match only on **`aspects_eq`** will not match `/arcane/recommend` unless Axiomurgy later adds aspects to the recommend body (documented gap).

First-class **Axiomurgy example** bundles (intent-only tiers, `target_skills: ["decide"]`):

| Bundle id | Example spell | Notes |
|-----------|---------------|--------|
| `axiomurgy_inbox_triage` | `examples/inbox_triage.spell.json` | Scope + HIGH + IRREVERSIBLE |
| `axiomurgy_openapi_rollback` | `examples/openapi_ticket_then_fail.spell.json` | Same risk class, distinct scope |
| `axiomurgy_research_stage` | `examples/research_brief.spell.json` | MEDIUM + PARTIAL (medium risk + write) |

## Where the seam is still approximate

- **Single aggregate intent** for the whole spell; step-level scopes in `vermyth_export`’s semantic program differ from this probe.
- **No graph encoding** in recommend input (dependencies, rune names, rollback) unless added later.
- **No per-rule miss reasons** from Vermyth when nothing matches; use [`scripts/eval_semantic_recommendations.py`](../scripts/eval_semantic_recommendations.py) heuristics and raw JSON.
- **Gate `aspects`** are not yet derived from the spell graph.

## Evaluation harness

From an Axiomurgy checkout:

```bash
python scripts/eval_semantic_recommendations.py --offline
```

prints the exact probe inputs and git pins (no server).

With Vermyth HTTP listening:

```bash
set AXIOMURGY_VERMYTH_BASE_URL=http://127.0.0.1:7777
python scripts/eval_semantic_recommendations.py --json
```

Report includes `recommendation_count`, `bundle_id`, `match_kind`, `strength` per row, plus metadata (`axiomurgy_git`, sibling `vermyth_git` when present).

A checked-in offline snapshot: [`artifacts/eval_semantic_recommend_offline_wiring.json`](../artifacts/eval_semantic_recommend_offline_wiring.json).

## Next highest-leverage manifest

**Prerequisite (done in this pass):** default `skill_id=decide` plus `spell_level_vermyth_intent` so tiers can match real scopes.

**Next:** add a fourth bundle for **`primer_to_axioms`** (long curriculum publish) or extend evaluation to **spellbook entrypoints** once recommend probes support spellbook context—the largest remaining blind spot is **multi-spell / spellbook** workflows without duplicating scope-only hacks.
