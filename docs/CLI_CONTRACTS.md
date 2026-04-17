# CLI contracts (stability reference)

This document describes **current** command-line behavior for automation and CI. It is not a feature specification; behavior changes should be paired with tests and release notes.

## Invocation

Supported entrypoints:

| Method | Example |
|--------|---------|
| Repository shim | `python axiomurgy.py <target> [options]` (run from clone; resolves via script path) |
| Module | `python -m axiomurgy <target> [options]` |
| Package API | `from axiomurgy.cli import main` then `raise SystemExit(main(sys.argv[1:]))` |

Install the package in editable mode so `import axiomurgy` resolves (`pip install -e ".[dev]"` from the repository root).

Schemas and the default policy are loaded from **`axiomurgy/bundled/`** inside the installed package (wheel-safe). Repository-root copies of the same JSON files may exist for documentation and parity checks.

## Streams

- **Success paths** and most **handled errors** use `print(...)` to **stdout**.
- **stderr** is typically empty for normal CLI operation. (Uncaught exceptions may write tracebacks to stderr.)

## Exit codes (`main()` return value)

| Code | Meaning |
|------|---------|
| 0 | Normal completion. For execution modes, the result JSON may still include `"status": "failed"` for a failed spell run; the process exits 0 unless an exception escapes the handler below. |
| 1 | Handled exception types (e.g. validation, `AxiomurgyError`, JSON decode errors, HTTP errors from adapters). Error line: `ERROR: ...` on **stdout**. |
| 2 | Missing target, bad flag combinations, or CLI usage errors. `ERROR: ...` on **stdout**. |
| 3 | `--verify-review-bundle` when status is not `exact`/`partial`; Ouroboros replay drift. |
| 4 | Ouroboros replay `non_replayable`. |

## Structured failure without nonzero exit

- **`--lint`**: Invalid JSON may produce `"ok": false` and structured `errors` with exit code **0**.
- **Execution** (`execute_spell` via CLI): Failed runs often return JSON with `"status": "failed"` and exit code **0** unless an exception is raised during orchestration.

Shell scripts should not rely on exit code alone for execution success; inspect JSON `"status"` (and spellbook-specific fields) when needed.

## Path resolution

Targets and `--policy` / `--artifact-dir` paths are resolved by the runtime; tests should pass **absolute paths** when `cwd` is not the repository root.
