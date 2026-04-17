# Vermyth gate contract (reference runtime)

This document locks **observable** behavior for the optional policy block `vermyth_gate` and related CLI semantics. It is paired with tests under `tests/test_vermyth_*.py`.

## Policy shape

Bundled default (disabled) lives in `axiomurgy/bundled/policies/default.policy.json` under `vermyth_gate`:

| Field | Meaning |
|-------|---------|
| `enabled` | If false, the gate is not invoked (`run_vermyth_gate` returns `status: skipped`). |
| `mode` | `advisory`, `policy_input`, or `hard_stop` (see below). |
| `timeout_ms` | Passed to the HTTP client as the request timeout for `POST .../tools/decide`. |
| `on_timeout` | `allow` or `deny`. On **any** failed HTTP call to Vermyth (including connection errors), or when `deny` and the base URL is **missing**, the runtime either returns an error record / `unavailable` or raises (see below). The name `on_timeout` is historical: it applies to transport failures broadly, not only literal timeouts. |
| `on_incoherent` | `allow` or `deny`. Used only in `hard_stop` when Vermyth returns `decision.action == DENY`. |

## Modes

| Mode | Vermyth `decision.action` | Effect |
|------|---------------------------|--------|
| `advisory` | Any | Never raises for policy decision alone. Result JSON may include `vermyth_gate` on successful HTTP. No merge into Axiomurgy policy reasons. |
| `policy_input` | Any | Never raises for policy decision alone. On HTTP success, a single line of notes is merged into the **first** `evaluate_policy` / `PolicyDecision.reasons` list (prefix `vermyth_gate:`). |
| `hard_stop` | `DENY` | If `on_incoherent` is `deny`, raises `SpellValidationError` before spell steps run. If `on_incoherent` is `allow`, execution continues. |
| (any) | `RESHAPE` or other non-`DENY` | Not treated as hard-stop; only `DENY` triggers hard-stop when mode and `on_incoherent` match. |

## HTTP and environment

- Requires `AXIOMURGY_VERMYTH_BASE_URL` or `VERMYTH_BASE_URL` unless the gate will only return `unavailable` / error records.
- Request: `POST {base}/tools/decide` with JSON from `_decide_payload` (intent, aspects, effects) in `axiomurgy/vermyth_integration.py`.

## CLI failure contract

When `run_vermyth_gate` raises `SpellValidationError` (hard-stop, or `on_timeout: deny` with missing URL / HTTP failure), `main()` in `axiomurgy/legacy.py`:

- Prints `ERROR: ...` to **stdout** (not stderr).
- Returns exit code **1**.
- Does **not** print a JSON result document for that failure path.

This matches other handled `AxiomurgyError` subclasses.

## Attestation and `compare_reviewed_bundle`

`compare_reviewed_bundle` in `axiomurgy/review.py` compares a **fixed** set of paths (environment, fingerprints, capability envelope). It does **not** diff arbitrary `plan.*` keys today.

The helper `_attestation_allowlisted_path` is **defensive**: if future diffs add paths like `plan.semantic_recommendations`, those paths are allowlisted so optional Vermyth blocks do not false-mismatch attestation. Prefixes such as `plan.semantic_recommendations` also match nested keys under that prefix.

**Compatibility note:** New plan fields that are not part of the fixed compare set are invisible to attestation unless compare logic is extended.

## Execution paths (replay vs execute vs Ouroboros)

When `main` resolves a target:

1. **Replay** (`--replay-revolution-dir` / `--replay-run-manifest`): branches to `replay_ouroboros_revolution` **before** `run_vermyth_gate`. No Vermyth gate HTTP call, no `vermyth_gate` on that JSON.
2. **Describe / plan / lint / review-bundle / verify**: no gate (gate runs only in the execution branch below).
3. **Execution** (`--cycle-config` absent): `run_vermyth_gate` runs, then `execute_spell`. Result may include `vermyth_gate` and optional receipt paths.
4. **Ouroboros** (`--cycle-config`): `run_vermyth_gate` still runs first (network side effect if enabled), then `ouroboros_chamber`. When the gate is not skipped (same rule as plain execution: `vermyth_gate` omitted when `status == skipped`), the cycle result JSON includes **`vermyth_gate`**, optional **`vermyth_gate_path`** (sidecar `<spell>.vermyth_gate.json` under the run artifact root), and the same fields on **`*.ouroboros.json`** / **`run_manifest`** witnesses. **`vermyth_receipt_path`** may appear when receipt emission is enabled, from the **baseline** `execute_spell` inside the chamber (veil executions do not re-run the gate or merge `policy_input` notes).

See [CLI_CONTRACTS.md](CLI_CONTRACTS.md) for exit codes and streams.

## Live integration tests

Optional HTTP tests against a real Vermyth server are marked `vermyth_live` and run only when `AXIOMURGY_VERMYTH_LIVE=1` and a base URL is set. See `tests/integration/test_vermyth_live.py` and `pytest.ini`. Pin the Vermyth version you validated (PyPI or Git tag) in commit messages; optional dependency hints live in `requirements-vermyth-integration.txt`.

**Semantic recommendations** (separate from `vermyth_gate`): live HTTP baseline compare is documented in [`SEMANTIC_RECOMM_VERMYTH_PIN.md`](SEMANTIC_RECOMM_VERMYTH_PIN.md) and enforced by [`.github/workflows/semantic_recommend_baseline.yml`](../.github/workflows/semantic_recommend_baseline.yml) when secrets are set.

## Ranked compatibility risks

1. **Medium:** Ouroboros with `vermyth_gate.enabled` triggers an extra HTTP call every invocation (plus baseline execution work inside the chamber).
2. **Medium:** Vermyth API drift for `decide` / HTTP paths.
3. **Medium:** `on_timeout` naming vs behavior (missing URL and HTTP errors use the same `deny` branch).
4. **Low:** Allowlist prefix `plan.semantic_recommendations` could shadow a future sibling key if both were compared.
5. **Low:** Operators may expect `RESHAPE` to block; only `DENY` is a hard-stop trigger.
