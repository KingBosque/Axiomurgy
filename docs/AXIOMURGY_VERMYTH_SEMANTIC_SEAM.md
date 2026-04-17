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

### Supported vs unsupported (families)

| Stability | Meaning |
|-----------|---------|
| **Supported (catalogued)** | Spell appears in [`docs/data/semantic_recommend_corpus.json`](../docs/data/semantic_recommend_corpus.json) with non-empty `must_include` / `primary_bundle_id` and an `axiomurgy_*` bundle id in that row. Calibrated in harness and (when applicable) in [`docs/reports/compatibility_baseline_live_v1.json`](../docs/reports/compatibility_baseline_live_v1.json). |
| **Unsupported / exploratory** | Any spell not listed there, or workflows without an `axiomurgy_*` bundle in the Vermyth catalog. **No semantic bundle guarantee**: recommendations may be empty, generic, or drift as the catalog changes. |

## Known no-match cases (expected)

- **Negative controls**: probes with scopes like `axiomurgy:calibration_readonly_low_risk` are designed not to match any `axiomurgy_*` **exact** tier; with current tiers they typically yield **zero** recommendations (no false positive from Axiomurgy-aligned bundles). Generic Vermyth bundles that require `aspects_eq` or `semantic_bundle` scope also do not match Axiomurgy `/arcane/recommend` bodies (see full matrix below).

## All `decide` bundles that define `recommendation`

Regenerate with (from Axiomurgy repo root):

```bash
python scripts/dump_bundle_recommend_matrix.py
```

Snapshot (predicate summary; `scope semantic_bundle` means some tier uses `intent_subset_eq` with `scope: semantic_bundle`):

| bundle_id | target_skills | aspects_eq | thresholds_eq | scope semantic_bundle | tier ops (union) |
|-----------|---------------|------------|---------------|----------------------|------------------|
| `axiomurgy_inbox_triage` | decide | false | false | false | `intent_subset_eq`, `objective_length_between` |
| `axiomurgy_openapi_rollback` | decide | false | false | false | `intent_subset_eq` |
| `axiomurgy_ouroboros_score_fixture` | decide | false | false | false | `intent_subset_eq` |
| `axiomurgy_ouroboros_score_fixture_v12` | decide | false | false | false | `intent_subset_eq` |
| `axiomurgy_primer_codex_publish` | decide | false | false | false | `intent_subset_eq` |
| `axiomurgy_primer_to_axioms` | decide | false | false | false | `intent_subset_eq` |
| `axiomurgy_primer_via_mcp` | decide | false | false | false | `intent_subset_eq` |
| `axiomurgy_research_stage` | decide | false | false | false | `intent_subset_eq`, `objective_length_between` |
| `coherent_probe` | decide | true | false | true | `aspects_eq`, `intent_subset_eq`, `objective_length_between`, `objective_starts_with` |
| `divination_gate` | decide | true | false | true | `aspects_eq`, `field_eq`, `field_present`, `intent_subset_eq`, `objective_starts_with` |
| `network_edge_ward` | decide | true | true | true | `aspects_eq`, `intent_subset_eq`, `objective_starts_with`, `thresholds_eq` |
| `resonance_ping_cast` | cast | true | false | true | `aspects_eq`, `intent_subset_eq`, `objective_starts_with` |
| `strict_ward_probe` | decide | true | true | true | `aspects_eq`, `intent_subset_eq`, `objective_starts_with`, `thresholds_eq` |

**Axiomurgy HTTP probe** sends nested `intent` only (no top-level `aspects` / `thresholds`). Rows with `aspects_eq`, `thresholds_eq`, or `scope semantic_bundle` are **unlikely** to match the current probe unless Axiomurgy extends the recommend body. **`axiomurgy_*`** rows are **intent-only** (`axiomurgy:{spell.name}` scopes)—aligned with [`spell_level_vermyth_intent`](../axiomurgy/vermyth_export.py).

### Ambiguity and tie-break

Two bundles can both expose advisory tiers that only pin `scope` (cosmetic / near-duplicate risk). Today, Axiomurgy spell names map to **unique** `axiomurgy:{spell.name}` scopes, so cross-talk between different `axiomurgy_*` bundles is low. When multiple tiers match, Vermyth sorts recommendations by **`(-strength, bundle_id)`** ([`Vermyth/vermyth/arcane/recommend.py`](../../Vermyth/vermyth/arcane/recommend.py)).

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
- **Live HTTP baseline (v1)** — committed file [`docs/reports/compatibility_baseline_live_v1.json`](../docs/reports/compatibility_baseline_live_v1.json), schema [`docs/reports/compatibility_baseline_v1.schema.json`](../docs/reports/compatibility_baseline_v1.schema.json). Refresh `captured_at`, git SHAs, and optional `recommendations_fingerprint` entries after a live run on the corpus spell list:

  ```bash
  python scripts/eval_semantic_recommendations.py --write-baseline docs/reports/compatibility_baseline_live_v1.json
  ```

  Compare a live run to the baseline (exit **1** on regression; stderr shows failures):

  ```bash
  python scripts/eval_semantic_recommendations.py --compare-baseline docs/reports/compatibility_baseline_live_v1.json
  ```

  Use `--allow-sha-drift` when git pins intentionally differ locally.

### Acceptance thresholds

See [`docs/SEMANTIC_RECOMMEND_ACCEPTANCE.md`](SEMANTIC_RECOMMEND_ACCEPTANCE.md) for gates (positive top-1, negative FP, multi-match rate, transport errors).

### Plan output: human brief

When recommendations are fetched, [`fetch_semantic_recommendations`](../axiomurgy/vermyth_integration.py) adds **`summary`** (one line), **`rows`** (top bundles with optional `inspect_hint`), and **`advisory_note`** when not `ok`. These live under `plan.semantic_recommendations.*` and remain covered by the review-bundle prefix allowlist in [`axiomurgy/review.py`](../axiomurgy/review.py).

## Next manifests

**Default: none** for this governance pass. Add or adjust an `axiomurgy_*` Vermyth bundle only when a **live** baseline or corpus run shows a persistent `no_match` / `wrong_match` for a **positive** corpus spell after verifying URL and token—that is, evidence that the catalog lacks coverage for that family. If you add a bundle, extend the corpus line, bump **baseline_version** (or document the bump), and refresh [`compatibility_baseline_live_v1.json`](../docs/reports/compatibility_baseline_live_v1.json).

Further refinements:

- Optional CI job that runs `--compare-baseline` when `AXIOMURGY_VERMYTH_BASE_URL` is available.
- Tighten **advisory** tiers only when calibration shows a wrong top-1.
