# Canonical contract files (schemas and default policy)

## Source of truth

The runtime loads JSON **only** from inside the package:

| Role | Path (under repository) |
|------|-------------------------|
| Spell JSON Schema | `axiomurgy/bundled/spell.schema.json` |
| Spellbook JSON Schema | `axiomurgy/bundled/spellbook.schema.json` |
| Default policy | `axiomurgy/bundled/policies/default.policy.json` |

Edit these files when changing contracts. Wheels and editable installs both resolve paths via `axiomurgy.util` (`PACKAGE_ROOT / "bundled" / ...`).

## Repository mirrors

The following paths are **byte-identical mirrors** of the bundled files above. They exist for stable URLs, browsing in the repo root, and docs that reference top-level paths:

- `spell.schema.json`
- `spellbook.schema.json`
- `policies/default.policy.json`

**Do not** edit only the mirror and expect the runtime to change: edit **bundled** first, then refresh mirrors (see below).

## Sync and CI

- After changing bundled files, run from the repo root:

  ```bash
  python scripts/sync_contract_mirrors.py
  ```

- CI runs `python scripts/sync_contract_mirrors.py --check` so mirrors cannot drift from bundled content.

## Related docs

- [CLI_CONTRACTS.md](CLI_CONTRACTS.md) — invocation, exit codes, `--artifact-dir` behavior.

## Remaining release risks (ranked)

| Severity | Risk |
|----------|------|
| Medium | **Default artifact directory** when using a bare install: `DEFAULT_ARTIFACT_DIR` is `ROOT / "artifacts"` where `ROOT` is the parent of the `axiomurgy` package, so it may point next to `site-packages`, not a project folder. Prefer passing **`--artifact-dir`** for predictable locations. See CLI_CONTRACTS.md. |
| Medium | **Bypassing CI** or editing mirrors only: mitigated by docs + sync script; residual risk if checks are skipped locally. |
| Low | **Line endings** on Windows: sync and `--check` use binary copy/compare; avoid re-saving mirrors in an editor that changes bytes without intent. |
| Low | **Dual paths in prose** (README vs `axiomurgy/bundled/`): prefer linking to this file or bundled paths for “where to edit.” |
