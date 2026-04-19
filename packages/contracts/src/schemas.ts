import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Repo root: packages/contracts/src -> ../../.. */
export const REPO_ROOT = join(__dirname, "..", "..", "..");

/**
 * Spell/spellbook schemas: same files the Python runtime loads (see docs/CONTRACT_FILES.md).
 * Repo-root `spell.schema.json` mirrors are kept in sync via `scripts/sync_contract_mirrors.py --check` in CI.
 */
export function readSpellSchema(): object {
  return JSON.parse(readFileSync(join(REPO_ROOT, "axiomurgy", "bundled", "spell.schema.json"), "utf8")) as object;
}

export function readSpellbookSchema(): object {
  return JSON.parse(readFileSync(join(REPO_ROOT, "axiomurgy", "bundled", "spellbook.schema.json"), "utf8")) as object;
}

export function readCompatibilityBaselineSchema(): object {
  return JSON.parse(
    readFileSync(join(REPO_ROOT, "docs", "reports", "compatibility_baseline_v1.schema.json"), "utf8"),
  ) as object;
}
