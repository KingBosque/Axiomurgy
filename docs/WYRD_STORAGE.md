# Wyrd v1 storage (experimental)

Wyrd is an **append-only, read-mostly causal memory** layer stored next to spell artifacts. It is **not** execution authority: it does not influence `compile_plan`, policy, fingerprints, Vermyth, or attestation.

## Location

- SQLite database: **`<artifact-dir>/wyrd/graph.sqlite`**
- The `artifact_dir` is the resolved spell run’s artifact directory (same as habitus / witnesses in typical workflows).

## Gating

All of the following must be enabled:

1. `AXIOMURGY_REASONING=1`
2. `AXIOMURGY_REASONING_EXPERIMENTAL=1`
3. `AXIOMURGY_WYRD=1`

**Plan-time writes** append a snapshot after `--plan` reasoning is built. **`--describe` does not append** (no full plan bundle in the same shape); reads still surface bounded **`reasoning.experimental.wyrd_hints`** when the flags above are set.

## Semantics

- **Append-only**: new nodes and edges are inserted; v1 does not mutate or delete prior rows through the API.
- **Inspectable**: tables `wyrd_nodes` and `wyrd_edges` hold operational kinds and JSON payloads (see `axiomurgy/wyrd/model.py`, `axiomurgy/wyrd/snapshot.py`).
- **Bounded hints**: JSON output under `reasoning.experimental.wyrd_hints` is summarized (recent nodes/edges, related prior runs, consistency notes)—not a full dump.

## Privacy / ops

- Data is **local to the artifact directory** on disk. Treat the DB like any other artifact: copy, redact, or exclude from sharing as needed.
