import { existsSync } from "node:fs";
import { dirname, join } from "node:path";

/** Walk up until spell.schema.json (Axiomurgy repo root). */
export function findRepoRoot(startDir: string): string {
  let dir = startDir;
  for (let i = 0; i < 12; i++) {
    if (existsSync(join(dir, "spell.schema.json"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error("Could not find Axiomurgy repo root (spell.schema.json). Run from inside the repo.");
}
