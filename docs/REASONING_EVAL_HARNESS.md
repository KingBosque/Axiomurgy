# Reasoning efficacy evaluation harness

This repository includes an **offline, Python-first harness** to compare advisory **reasoning** outputs across explicit **evaluation modes**. It answers whether the current stack (telos/governor/dialectic, experimental correspondence/friction, Wyrd hints, Parthenogenesis candidates, Lullian verification) is **useful in plan JSON**, not whether execution succeeds.

**Non-goals:** The harness is **not** a planner, does **not** change `compile_plan`, `evaluate_policy`, fingerprints, Vermyth, or attestation, and does **not** execute spells or mutate spell files. It only calls `build_plan_summary` (same path as `--plan` reasoning attachment) under isolated artifact directories.

## Evaluation modes (explicit env contracts)

Modes are defined in `axiomurgy/reasoning_eval/modes.py` as named flag sets (no ad hoc env juggling):

| Mode | Meaning |
|------|---------|
| `baseline` | Reasoning off (env keys cleared) |
| `core_reasoning` | `AXIOMURGY_REASONING=1` only |
| `experimental_structure` | Reasoning + experimental (no generation) |
| `generation_only` | Reasoning + experimental + Parthenogenesis (`AXIOMURGY_REASONING_GENERATION=1`), no Lullian |
| `generation_ranked` | Above + Lullian (`AXIOMURGY_REASONING_LULLIAN=1`) |
| `generation_ranked_wyrd` | Above + Wyrd (`AXIOMURGY_WYRD=1`) |

## Corpus

Default corpus: `corpus/reasoning_eval_corpus.json`. Each entry has:

- `path`: repo-relative spell path
- `family`: coarse category (e.g. read-heavy, external-boundary, approval)
- `expect` (optional): soft expectations such as `no_candidate_expected`, `candidates_expected`, `likely_preferred_kinds`, `wyrd_expected_to_matter`

Add spells by appending objects; keep the file small and real.

## Running

```bash
python scripts/eval_reasoning_efficacy.py --json --write-report artifacts/reasoning_eval/latest
```

Flags:

- `--corpus PATH` — corpus JSON (default: `corpus/reasoning_eval_corpus.json`)
- `--modes baseline,core_reasoning,...` — comma-separated subset (default: all modes)
- `--json` — print full JSON report to stdout
- `--write-report PREFIX` — write `PREFIX.json` and `PREFIX.md`
- `--include-raw` — include full `raw_plan` per row (large)
- `--labels PATH` — optional human labels sidecar (see below)
- `--artifact-dir PATH` — base for per-run isolated artifact dirs (default: temp)
- `--limit N` — first N corpus spells only

If the Wyrd SQLite DB is missing, evaluation **continues** with empty Wyrd hints (honest “no memory” behavior).

## Metrics (bounded / heuristic)

Aggregates are in `metrics_by_mode` in the JSON report. Names include:

- Presence: `reasoning_presence_rate`, `experimental_presence_rate`, `candidate_generation_rate`, `no_candidate_rate`, `error_rate`
- Distributions: `candidate_kind_distribution`, `preferred_candidate_kind_distribution`
- Preference: `preferred_candidate_rate`
- Improvement **signals** (from Lullian dimension statuses or empty if not applicable):  
  `friction_improvement_signal_rate`, `boundary_isolation_improvement_signal_rate`, `approval_positioning_improvement_signal_rate`, `objective_alignment_signal_rate`
- Wyrd: `wyrd_usage_rate` (non-empty hints when Wyrd is enabled)
- Quality checks: `overgeneration_rate` (spells with `expect.no_candidate_expected` but `candidate_count > 0`), `ranking_decisiveness_rate` (Lullian top status `preferred`)

Cross-mode block `cross_mode_metrics` includes `wyrd_preferred_kind_changed_count` when comparing `generation_ranked` vs `generation_ranked_wyrd`.

These are **not** learned scores—lexicographic / categorical summaries for human review.

## Optional human labels

Sidecar JSON (list or `{ "labels": [...] }`) with entries like:

```json
{
  "spell_path": "examples/openapi_ticket_then_fail.spell.json",
  "human_preferred_candidate_kind": "risk_reduction_variant",
  "human_verdict": "agree",
  "human_notes": "optional"
}
```

Paths may be repo-relative or absolute; they are normalized to absolute keys for matching. When present, the report includes `human_agreement` (e.g. agreement rate vs Lullian `preferred_candidate_kind` on `generation_ranked`).

## Artifacts

Reports default to paths like `artifacts/reasoning_eval/<prefix>` when you pass `--write-report`. These are **not** attested execution artifacts.

## Tests

`tests/test_reasoning_eval_harness.py` covers corpus load, mode flags, baseline vs generation behavior, metrics determinism, reports, optional labels, no Vermyth calls, unchanged spell mtimes, and Wyrd delta bookkeeping.
