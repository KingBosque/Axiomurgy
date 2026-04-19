# Reasoning surfaces (minimal vs experimental)

Optional **`reasoning`** on `--describe` / `--plan` has two **intended** shapes. Use env vars to select; defaults keep the feature off.

## Environment quick reference

| Surface | Required env |
|---------|----------------|
| **Minimal advisory** | `AXIOMURGY_REASONING=1` |
| **Experimental advisory** | `AXIOMURGY_REASONING=1` and `AXIOMURGY_REASONING_EXPERIMENTAL=1` (optional: `AXIOMURGY_WYRD=1` for Wyrd v1; optional: `AXIOMURGY_REASONING_GENERATION=1` for Parthenogenesis v1 candidates; optional: `AXIOMURGY_REASONING_LULLIAN=1` for Lullian v1 verification over those candidates) |

### CLI examples (minimal)

```bash
export AXIOMURGY_REASONING=1
python -m axiomurgy path/to/spell.spell.json --plan
```

Windows PowerShell:

```powershell
$env:AXIOMURGY_REASONING="1"
python -m axiomurgy path/to/spell.spell.json --plan
```

### CLI examples (experimental)

```bash
export AXIOMURGY_REASONING=1
export AXIOMURGY_REASONING_EXPERIMENTAL=1
# optional: export AXIOMURGY_WYRD=1
# optional: export AXIOMURGY_REASONING_GENERATION=1
# optional: export AXIOMURGY_REASONING_LULLIAN=1
python -m axiomurgy path/to/spell.spell.json --plan
```

## Shadow-mode core (telos / governor / dialectic)

When **`AXIOMURGY_REASONING=1`**, `--describe` and `--plan` attach **advisory** traces only. They use the **same compiled plan** as static policy (same step rows as `--plan`), but **do not** change execution, `compile_plan` ordering, `evaluate_policy` at runtime, fingerprints, or attestation required paths.

- **`telos`**: `kind` is `declared` if `spell.constraints.telos` / `spell.inputs.telos` supplies a final cause or objectives; otherwise `derived`. Includes **`concern_rings`** (fixed catalog), **`distance_to_goal`** (0..1 heuristic from writes / externals / unmet approvals), and per-step **`step_scores`** (ring impact + `reversibility`). **Oikeiôsis** is represented only as concern rings inside **`telos`**, not as a separate top-level key.
- **`governor`**: `kind` `derived`; **`drives`**, **`constraints`**, **`mediator`**, **`tradeoffs`** — projection over spell + policy + static plan rows (not a second policy engine).
- **`dialectic`**: `kind` `derived`; one **`episodes`** entry with **`thesis`**, **`antithesis`**, **`synthesis`**, **`tensions`**, **`selection_basis`** — deterministic strings from the same plan/telos/governor inputs (no LLM).

Heuristic formulas live in `axiomurgy/telos.py` and `axiomurgy/governor.py` module docstrings.

## Experimental advisory (correspondence / friction)

When **`AXIOMURGY_REASONING_EXPERIMENTAL=1`** (with **`AXIOMURGY_REASONING=1`**), **`reasoning.experimental.correspondence`** and **`reasoning.experimental.friction`** add **deterministic, bounded heuristics** — not execution authority, not policy, not a second planner.

- **`correspondence`** (`axiomurgy/correspondence.py`): clusters plan steps by operational role (effect, external boundary, approval, write surface), links clusters to existing **`telos.objectives`**, lists **`correspondence_rules`** applied, and may list **`repeated_patterns`** only when there is evidence (e.g. two disjoint read→…→write pipelines). Empty or minimal output when structure does not support richer motifs.
- **`friction`** (`axiomurgy/friction.py`): per-step and overall **0..1** heuristic scores from telos step components plus explicit deltas (external/write/approval/coupling; optional dialectic-unresolved bump on risky steps), **`risk_factors`**, **`contingency_notes`**, **`fallback_absence`** vs spell rollback, and **`bottlenecks`**. Interpretation bands: low / medium / high.

## Wyrd v1 (experimental causal memory)

When **`AXIOMURGY_WYRD=1`** in addition to reasoning + experimental, **`reasoning.experimental.wyrd_hints`** returns **bounded summaries** of recent nodes/edges and optional **related prior runs** for the same spell. **Plan** (`--plan`) **append-only** persists a compact graph snapshot to **`<artifact-dir>/wyrd/graph.sqlite`**. **`--describe` does not write** to Wyrd; it may still **read** hints if the DB exists.

Wyrd v1 is **advisory memory only**—not a planner, not a policy engine, and not part of attested review comparison. Storage layout and mapping rules: [WYRD_STORAGE.md](WYRD_STORAGE.md), `axiomurgy/wyrd/snapshot.py`.

## Parthenogenesis v1 (experimental generation candidates)

When **`AXIOMURGY_REASONING_GENERATION=1`** (with reasoning + experimental), **`reasoning.experimental.generation_candidates`** holds a **small bounded list** (default cap 3) of **non-executable** candidate offspring: `subgoal_split`, `risk_reduction_variant`, `approval_first_variant`, `boundary_isolation_variant`. Each candidate is **review-bound** (`review_required: true`, `execution_ready: false` in v1). Generation uses telos, governor/dialectic tensions, correspondence clusters, friction bottlenecks, and optional Wyrd node ids as **supporting references only**—no auto-selection, no spell file writes, no LLM. **`--plan`** is the supported path; **`--describe`** returns an **empty** candidate list with an honest note (`plan_path_preferred_for_generation`). Implementation: `axiomurgy/generation.py`.

## Lullian v1 (experimental candidate verification)

When **`AXIOMURGY_REASONING_LULLIAN=1`** together with reasoning, experimental, and **`AXIOMURGY_REASONING_GENERATION=1`**, **`reasoning.experimental.candidate_verification`** provides a **bounded, symbolic** comparison of the compiled plan (**`base_plan`**) against each Parthenogenesis candidate on a **fixed eight-dimension wheel** (telos coverage, concern rings, friction, boundaries, approval placement, reversibility, correspondence, Wyrd consistency). Output includes per-dimension categorical statuses (`improves` / `preserves` / `regresses` / `unknown` / `contradicts`), aggregate counts, a deterministic **lexicographic rank**, and an explicit advisory **`selection_note`**—it does **not** search for new candidates, does **not** mutate plans, and does **not** auto-select or execute anything. **`axiomurgy/combinatorics.py`** remains the separate advisory combinatorics stub; Lullian verification lives in **`axiomurgy/lullian.py`**. If Lullian is off, **`candidate_verification`** is omitted. If generation is off, **`candidate_verification`** is omitted even when Lullian is on. With Lullian + generation on, **`--describe`** may still include a **minimal** shell (empty **`candidate_results`**, note `plan_path_preferred_for_verification`); full verification uses **`--plan`**.

Example shape (abbreviated):

```json
{
  "kind": "derived",
  "bounded": true,
  "dimension_order": ["telos_coverage", "concern_ring_impact", "friction_reduction", "boundary_isolation", "approval_positioning", "reversibility", "correspondence_preservation", "wyrd_consistency"],
  "base_plan": {
    "candidate_id": "base_plan",
    "dimension_results": [],
    "verification_status": "baseline"
  },
  "candidate_results": [],
  "selection_note": "…"
}
```

## JSON skeletons (side by side)

**Minimal advisory** — top-level `reasoning` keys only (no `experimental` object). Shapes below are abbreviated; see live `--plan` output for full fields.

```json
{
  "reasoning": {
    "axiomurgy_reasoning_version": "1.7.0",
    "classification": {
      "surface": "minimal_advisory",
      "derived_keys": ["dialectic", "governor", "habitus", "scene", "telos"],
      "habitus_role": "descriptive_context",
      "experimental_enabled": false,
      "experimental_keys": []
    },
    "governor": { "kind": "derived", "drives": [], "constraints": [], "mediator": {}, "tradeoffs": [] },
    "telos": {
      "kind": "derived",
      "shadow_mode": true,
      "final_cause": "…",
      "objectives": [],
      "concern_rings": [],
      "distance_to_goal": { "value": 0.0, "unit": "heuristic" },
      "step_scores": []
    },
    "dialectic": { "kind": "derived", "episodes": [] },
    "scene": {},
    "habitus": { "kind": "descriptive_context", "artifact_dir": "…", "policy_path": "…" }
  }
}
```

**Experimental advisory** — same minimal keys, plus **`experimental`** (flat map; no nested `classification` / `derived_keys` under it):

```json
{
  "reasoning": {
    "axiomurgy_reasoning_version": "…",
    "classification": {
      "surface": "minimal_advisory",
      "derived_keys": ["dialectic", "experimental", "governor", "habitus", "scene", "telos"],
      "habitus_role": "descriptive_context",
      "experimental_enabled": true,
      "experimental_keys": ["candidate_verification", "combinatorics_search", "correspondence", "friction", "generation_candidates", "wyrd_hints"]
    },
    "governor": {},
    "telos": { "final_cause": null, "objectives": [] },
    "dialectic": {},
    "scene": {},
    "habitus": {},
    "experimental": {
      "correspondence": {
        "kind": "derived",
        "clusters": [],
        "objective_links": [],
        "repeated_patterns": [],
        "correspondence_rules": []
      },
      "friction": {
        "kind": "derived",
        "overall_friction": { "value": 0.0, "unit": "heuristic_0_1", "interpretation": "low" },
        "per_step_friction": [],
        "bottlenecks": []
      },
      "combinatorics_search": {},
      "wyrd_hints": {
        "kind": "derived",
        "recent_nodes": [],
        "recent_edges": [],
        "related_prior_runs": [],
        "consistency_notes": []
      },
      "generation_candidates": {
        "kind": "derived",
        "bounded": true,
        "review_required": true,
        "candidates": [],
        "generation_enabled": false
      }
    }
  }
}
```

`classification.derived_keys` lists **top-level** keys under `reasoning` that the runtime treats as part of the derived surface; in experimental mode it is a **strict superset** of the minimal list (adds `experimental`). Values inside `reasoning.experimental.*` are intentionally **non-recursive**: no nested maturity taxonomies there.

See also [CLI_CONTRACTS.md](CLI_CONTRACTS.md), [WYRD_STORAGE.md](WYRD_STORAGE.md), and [VERMYTH_GATE.md](VERMYTH_GATE.md) (attestation allowlist for reasoning paths).

**Offline efficacy evaluation** (compare modes on a corpus, no execution): [REASONING_EVAL_HARNESS.md](REASONING_EVAL_HARNESS.md).
