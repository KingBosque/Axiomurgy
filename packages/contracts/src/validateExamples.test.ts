import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  REPO_ROOT,
  validateCompatibilityBaselineJson,
  validateSpellbookJson,
  validateSpellJson,
} from "./index.js";

const SPELL_EXAMPLES = [
  "examples/primer_to_axioms.spell.json",
  "examples/inbox_triage.spell.json",
  "examples/openapi_ticket_then_fail.spell.json",
];

describe("spell.schema.json", () => {
  it.each(SPELL_EXAMPLES)("validates %s", (rel) => {
    const raw = JSON.parse(readFileSync(join(REPO_ROOT, rel), "utf8"));
    const r = validateSpellJson(raw);
    expect(r.ok, JSON.stringify(r)).toBe(true);
  });
});

describe("spellbook.schema.json", () => {
  it("validates primer_codex spellbook", () => {
    const raw = JSON.parse(
      readFileSync(join(REPO_ROOT, "spellbooks", "primer_codex", "spellbook.json"), "utf8"),
    );
    const r = validateSpellbookJson(raw);
    expect(r.ok, JSON.stringify(r)).toBe(true);
  });
});

describe("compatibility_baseline_v1.schema.json", () => {
  it("validates committed live baseline", () => {
    const raw = JSON.parse(
      readFileSync(join(REPO_ROOT, "docs", "reports", "compatibility_baseline_live_v1.json"), "utf8"),
    );
    const r = validateCompatibilityBaselineJson(raw);
    expect(r.ok, JSON.stringify(r)).toBe(true);
  });
});
