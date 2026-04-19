import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Repo root: packages/contracts/src -> ../../.. */
export const REPO_ROOT = join(__dirname, "..", "..", "..");

export function readSpellSchema(): object {
  return JSON.parse(readFileSync(join(REPO_ROOT, "spell.schema.json"), "utf8")) as object;
}

export function readSpellbookSchema(): object {
  return JSON.parse(readFileSync(join(REPO_ROOT, "spellbook.schema.json"), "utf8")) as object;
}

export function readCompatibilityBaselineSchema(): object {
  return JSON.parse(
    readFileSync(join(REPO_ROOT, "docs", "reports", "compatibility_baseline_v1.schema.json"), "utf8"),
  ) as object;
}
