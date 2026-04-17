# Semantic baseline — maintainer runbook (10 lines)

1. **Run the live gate in CI** (requires repo secret `VERMYTH_HTTP_URL`, optional `VERMYTH_HTTP_TOKEN`): *Actions* → *Semantic recommend baseline* → *Run workflow* on `master`/`main`, or `gh workflow run semantic_recommend_baseline.yml --ref master`.
2. **This environment cannot run your secrets**—trigger the workflow on GitHub or export `AXIOMURGY_VERMYTH_BASE_URL` / token locally and run the same command as the workflow (see workflow YAML).
3. **Refresh** [`compatibility_baseline_live_v1.json`](reports/compatibility_baseline_live_v1.json) when Vermyth manifests or [`semantic_recommend_corpus.json`](data/semantic_recommend_corpus.json) change on purpose, or when recording stable `recommendations_fingerprint` values after review.
4. **Do not refresh** for unrelated Axiomurgy-only commits, doc typos, or to silence failures without understanding drift; never add `--allow-sha-drift` to the primary CI step.
5. **Artifact inspection**: open the green/red run → job *compare_baseline* → *Artifacts* → download **`semantic-recommend-baseline-run`** → unzip → read `ci_semantic_recommend_baseline.json` (full probe + calibration) and `.md` summary.
6. **Pin ownership**: **Axiomurgy repo maintainers** bump `VERMYTH_GIT_REF` (and baseline `vermyth_git`) in lockstep with whoever deploys the Vermyth HTTP stack that serves those bundles; record coordination in the baseline refresh PR.
7. **Policy detail** (SHA drift, refresh steps): [`SEMANTIC_RECOMM_VERMYTH_PIN.md`](SEMANTIC_RECOMM_VERMYTH_PIN.md).
8. **Quick status** without a full probe: `python scripts/semantic_seam_status.py` (add `--probe` if base URL is set for `/healthz` only).
9. **Git tag** `semantic-seam-baseline-gate-r1` points at the commit that adds this runbook alongside the CI gate; use `…-r2` for later milestones if needed.
10. **Forks**: the workflow skips when `VERMYTH_HTTP_URL` is unset—expected; only the canonical repo with secrets gets the automated gate.
