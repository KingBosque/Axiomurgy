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

## Supported workflow families (recommend corpus)

Ground-truth and spell paths live in [`docs/data/semantic_recommend_corpus.json`](../docs/data/semantic_recommend_corpus.json). Each spell maps to an **expected primary** Axiomurgy-aligned bundle (intent-only `intent_subset_eq` on `scope` + `reversibility` + `side_effect_tolerance`).

| Family | Example spell path | Primary bundle id |
|--------|-------------------|-------------------|
| hitl_outbound | `examples/inbox_triage.spell.json` | `axiomurgy_inbox_triage` |
| openapi_rollback | `examples/openapi_ticket_then_fail.spell.json` | `axiomurgy_openapi_rollback` |
| research_stage | `examples/research_brief.spell.json` | `axiomurgy_research_stage` |
| primer_curriculum | `examples/primer_to_axioms.spell.json` | `axiomurgy_primer_to_axioms` |
| primer_curriculum | `examples/primer_via_mcp.spell.json` | `axiomurgy_primer_via_mcp` |
| primer_curriculum | `spellbooks/primer_codex/spells/publish_codex.spell.json` | `axiomurgy_primer_codex_publish` |
| meta_scoring | `examples/ouroboros_score_fixture.spell.json` | `axiomurgy_ouroboros_score_fixture` |
| meta_scoring | `examples/ouroboros_score_fixture_v12.spell.json` | `axiomurgy_ouroboros_score_fixture_v12` |
| negative_control | `examples/calibration/readonly_probe_low_risk.spell.json` | (none; must not top-rank aligned bundles) |
| negative_control | `examples/calibration/write_only_high_risk.spell.json` | (none) |

## Known no-match cases (expected)

- **Negative controls**: probes with scopes like `axiomurgy:calibration_readonly_low_risk` are designed not to match any `axiomurgy_*` **exact** tier; with current tiers they typically yield **zero** recommendations (no false positive from Axiomurgy-aligned bundles). Generic Vermyth bundles that require `aspects_eq` or `semantic_bundle` scope also do not match Axiomurgy `/arcane/recommend` bodies (see overlap table below).

## Overlap with generic Vermyth bundles (Axiomurgy probe shape)

For inputs produced by `spell_level_vermyth_intent` only (nested `intent`, no top-level `aspects` / `thresholds`):

| Bundle | Typical blocker for accidental Axiomurgy match |
|--------|-----------------------------------------------|
| `coherent_probe`, `divination_gate`, `strict_ward_probe` | `intent_subset_eq` expects `scope: semantic_bundle` and `aspects_eq` on recommend path fails (no aspects). |
| `network_edge_ward` | `aspects_eq` + `thresholds_eq` + objective prefix. |
| `resonance_ping_cast` | `target_skills: [cast]` — not evaluated for `skill_id: decide`. |
| Axiomurgy-aligned `axiomurgy_*` | `intent_subset_eq` on `axiomurgy:{spell.name}` — scoped; distinct spells do not cross-match at exact tier. |

Residual risk is **advisory** tiers that only pin `scope`: tier strength stays near the global `min_strength` floor; tighten in Vermyth if a calibration run shows a wrong top-1.

## Manifest matching

- Vermyth semantic bundles under `Vermyth/vermyth/data/arcane/bundles/` ship `recommendation.target_skills`. For Axiomurgy plan probes, **`skill_id` must appear in that list** (hence default `decide`).
- Bundles that match only on **`aspects_eq`** will not match `/arcane/recommend` unless Axiomurgy later adds aspects to the recommend body.

## Where the seam is still approximate

- **Single aggregate intent** for the whole spell; step-level scopes in `vermyth_export`’s semantic program differ from this probe.
- **No graph encoding** in recommend input (dependencies, rune names, rollback) unless added later.
- **No per-rule miss reasons** from Vermyth when nothing matches; use the harness heuristics and raw JSON.
- **Gate `aspects`** are not yet derived from the spell graph.

## Calibration and coverage

### Corpus-driven evaluation

[`docs/data/semantic_recommend_corpus.json`](../docs/data/semantic_recommend_corpus.json) lists spells, `family`, and `expect` (`must_include_bundle_ids`, `must_not_include_bundle_ids`, `primary_bundle_id`).

### Harness

```bash
python scripts/eval_semantic_recommendations.py --calibrate --json --write-report docs/reports/last_run
```

Uses [`docs/data/semantic_recommend_corpus.json`](../docs/data/semantic_recommend_corpus.json) for the spell list when `--spells` is omitted. Requires `AXIOMURGY_VERMYTH_BASE_URL` (or `--base-url`) for live HTTP. Produces `last_run.json` and `last_run.md` with **calibration_label** per spell: `correct_match` | `weak_but_plausible` | `wrong_match` | `no_match` | `error`.

**Labels (deterministic):**

- **correct_match** — Top recommendation has `match_kind` `exact` and `bundle_id` in `must_include_bundle_ids`.
- **weak_but_plausible** — Top has `match_kind` `advisory` and `bundle_id` in `must_include`.
- **wrong_match** — Top bundle is in `must_not_include`, or (positive case) top not in `must_include` when non-empty.
- **no_match** — Empty recommendation list.

Offline probe only (no Vermyth server):

```bash
python scripts/eval_semantic_recommendations.py --offline
```

### Pinned reports

- **In-process sanity check** (same matcher as HTTP body passed to `recommend_for_plain_invocation`; no TCP): [`docs/reports/semantic_recommend_calibration_inprocess.json`](../docs/reports/semantic_recommend_calibration_inprocess.json) records git pins and per-spell rows. Refresh when bundles change.
- **Live HTTP**: re-run the harness against your pinned Vermyth adapter and commit or archive the `PREFIX.json` output next to a dated note.

## Next refinements

- Optional **live** golden run checked in when CI or a release bot can reach Vermyth.
- Tighten **advisory** tiers only when calibration shows a wrong top-1.
