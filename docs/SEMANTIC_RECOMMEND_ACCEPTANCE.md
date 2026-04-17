# Semantic recommend acceptance (Axiomurgy ↔ Vermyth)

Semantic bundle recommendations are **advisory-only**. They do not change planning or execution. This document defines operator-facing **thresholds**, **multi-match** reporting, and **pass/fail** semantics aligned with the live HTTP harness and baseline comparison.

## Sources

- Corpus and expectations: [`docs/data/semantic_recommend_corpus.json`](data/semantic_recommend_corpus.json).
- Harness: [`scripts/eval_semantic_recommendations.py`](../scripts/eval_semantic_recommendations.py) (`--calibrate`, `--write-report`).
- Committed HTTP baseline (optional fingerprints): [`docs/reports/compatibility_baseline_live_v1.json`](reports/compatibility_baseline_live_v1.json), schema [`docs/reports/compatibility_baseline_v1.schema.json`](reports/compatibility_baseline_v1.schema.json).
- Pin policy, CI gate, and refresh rules: [`docs/SEMANTIC_RECOMM_VERMYTH_PIN.md`](SEMANTIC_RECOMM_VERMYTH_PIN.md).
- Status JSON (read-only): [`scripts/semantic_seam_status.py`](../scripts/semantic_seam_status.py).

## Labels (per spell run)

From the harness when `--calibrate` is used:

| Label | Meaning |
|-------|---------|
| `correct_match` | If `must_include_bundle_ids` is **empty**: any non-empty top recommendation (used for edge expectations). If **non-empty**: top `bundle_id` must be in `must_include` and `match_kind` must be `exact`. (All current corpus **positive** rows have non-empty `must_include`.) |
| `weak_but_plausible` | Top `bundle_id` in `must_include` but `match_kind` is `advisory` (not `exact`). |
| `wrong_match` | Top `bundle_id` is in `must_not_include`, or (positive case) top not in `must_include` when that list is non-empty. |
| `no_match` | Empty recommendation list. |
| `error` | Transport or HTTP adapter failure. |

See `classify_row` in the harness for exact rules.

## Suggested gates

| Metric | Gate | Notes |
|--------|------|-------|
| Positive top-1 | **100%** `correct_match` for corpus spells with non-empty `must_include` | Allow a **documented exception list** only during manifest migration. |
| Weak vs exact | Report separately | `weak_but_plausible` is acceptable if the advisory tier still surfaces the **expected** bundle; watch for regressions from `exact` to `advisory`. |
| Negative FP | **0** `wrong_match` | Top-1 must not be in `must_not_include_bundle_ids`. Empty recommendations on negatives are OK (true negative). |
| Multi-match ambiguity | **`multi_match_rate`** = fraction of runs with `recommendation_count` > 1 | **Informational** unless multiple recommendations share `match_kind == "exact"` for **different** bundles for one spell—then investigate manifest overlap. |
| HTTP / transport | **0** `error` in baseline runs | Optional retries for flaky CI. |

## Baseline comparison (`--compare-baseline`)

- Runs the same live probe as a normal harness invocation, then compares each corpus spell to [`compatibility_baseline_live_v1.json`](reports/compatibility_baseline_live_v1.json) (or another v1 file).
- **Exit code `1`** on regression; **stderr** prints the first failing line (spell or meta), then remaining failures.
- **`--allow-sha-drift`**: skip **both** `axiomurgy_git` and `vermyth_git` checks (local smoke only; not the primary CI policy).
- **`--allow-axiomurgy-sha-drift`**: allow `axiomurgy_git` to differ from the baseline (e.g. CI on any commit); still enforce **`vermyth_git`** when the baseline sets it. This is how [`.github/workflows/semantic_recommend_baseline.yml`](../.github/workflows/semantic_recommend_baseline.yml) runs.
- When `recommendations_fingerprint` is **non-null** in the baseline, the probe’s normalized recommendation list must match that fingerprint (see harness for normalization). With **null** fingerprints, only top bundle / negative rules / optional `expected_match_kind` apply.

## Tie-break (Vermyth)

When multiple bundles match, Vermyth orders recommendations by **`(-strength, bundle_id)`** (see `vermyth/arcane/recommend.py` in the Vermyth repo). Exact scopes for Axiomurgy spells are unique per `axiomurgy:{spell.name}` at the exact tier, so cross-talk between `axiomurgy_*` bundles is low for current corpus spells.
