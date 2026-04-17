# Vermyth pin and version-pair policy (semantic recommendations)

Semantic `/arcane/recommend` behavior is **advisory-only** and validated against a **committed HTTP baseline** ([`docs/reports/compatibility_baseline_live_v1.json`](reports/compatibility_baseline_live_v1.json)). This document is the single place for **which Vermyth git ref** CI uses and how to **refresh** the baseline safely.

## Supported pair

| Layer | What is pinned |
|-------|----------------|
| **Vermyth server** | HTTP deployment reachable at `AXIOMURGY_VERMYTH_BASE_URL` (CI: `VERMYTH_HTTP_URL` secret). Should be built from the same bundled `arcane/bundles` as the git ref below. |
| **Vermyth git** | Commit recorded in `compatibility_baseline_live_v1.json` as `vermyth_git`, and checked out in [`.github/workflows/semantic_recommend_baseline.yml`](../.github/workflows/semantic_recommend_baseline.yml) as `VERMYTH_GIT_REF` (must match). |
| **Axiomurgy git** | `axiomurgy_git` in the baseline is **informational** for the snapshot that produced the file. **CI does not require** the running commit to match that SHA: the gate uses `--allow-axiomurgy-sha-drift` so every push can be tested; **semantic** expectations (top bundle, match_kind, negatives) still must pass. |

## Harness metadata: `vermyth_git`

When comparing baselines, the harness reports `vermyth_git` from (in order):

1. `AXIOMURGY_VERMYTH_GIT_SHA` or `VERMYTH_GIT_SHA` (set explicitly, e.g. CI after `git -C vermyth rev-parse HEAD`)
2. `git rev-parse HEAD` in `../Vermyth` (sibling layout, e.g. local ARCANE checkout)
3. `git rev-parse HEAD` in `./vermyth` (nested clone under the Axiomurgy repo)

See [`scripts/eval_semantic_recommendations.py`](../scripts/eval_semantic_recommendations.py).

## SHA drift policy

| Flag | Use |
|------|-----|
| *(none)* | Full strict compare: `axiomurgy_git` and `vermyth_git` must match the baseline when those fields are non-null. |
| `--allow-axiomurgy-sha-drift` | **CI default**: allow any Axiomurgy commit; still enforce `vermyth_git` vs live metadata. |
| `--allow-sha-drift` | Local only: skip **both** git checks (e.g. quick smoke). **Do not use in the primary CI gate.** |

## Baseline refresh (operators)

1. **When**: After intentional Vermyth manifest changes, corpus changes, or tightening `recommendations_fingerprint`; not on unrelated Axiomurgy edits.
2. **How**: Against the pinned deployment, run:
   ```bash
   python scripts/eval_semantic_recommendations.py --calibrate --write-baseline docs/reports/compatibility_baseline_live_v1.json
   ```
3. **PR checklist**: Update `captured_at`, `axiomurgy_git`, `vermyth_git`, expectations/fingerprints as needed; bump `baseline_version` only if the [schema](reports/compatibility_baseline_v1.schema.json) or contract changes.
4. **CI**: Update `VERMYTH_GIT_REF` (and `VERMYTH_REPOSITORY` if needed) in `semantic_recommend_baseline.yml` so the checkout matches the new `vermyth_git` in the baseline file.

## CI workflow

[`.github/workflows/semantic_recommend_baseline.yml`](../.github/workflows/semantic_recommend_baseline.yml) runs when `VERMYTH_HTTP_URL` is configured. Forks without secrets skip the job (no failure).

## Uncovered families (corpus evidence)

All **positive** spells in [`docs/data/semantic_recommend_corpus.json`](data/semantic_recommend_corpus.json) map to an `axiomurgy_*` bundle listed in `axiomurgy_aligned_bundles`. **No uncovered positive family** is recorded in the corpus today. Spells **outside** the corpus are **unsupported / exploratory** for semantic guarantees (see [`AXIOMURGY_VERMYTH_SEMANTIC_SEAM.md`](AXIOMURGY_VERMYTH_SEMANTIC_SEAM.md)). New Vermyth manifests should follow only from evaluation evidence (persistent `no_match` / `wrong_match` on a positive corpus spell after verifying URL and token).
