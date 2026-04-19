import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { recommendInputPayload } from "./recommendPayload.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, "..", "..", "..");

describe("recommend input vs Python golden fixtures", () => {
  const fixtures = [
    "examples__primer_to_axioms",
    "examples__inbox_triage",
    "examples__openapi_ticket_then_fail",
  ];

  it.each(fixtures)("matches %s", (stem) => {
    const goldenPath = join(repoRoot, "docs", "fixtures", "ts-parity", `${stem}.json`);
    const golden = JSON.parse(readFileSync(goldenPath, "utf8")) as {
      spell_path: string;
      input_text: string;
      input: { intent: Record<string, string> };
    };
    const spellPath = join(repoRoot, ...golden.spell_path.split("/"));
    const doc = JSON.parse(readFileSync(spellPath, "utf8")) as Parameters<typeof recommendInputPayload>[0];
    const got = recommendInputPayload(doc);
    expect(got.inputText).toBe(golden.input_text);
    expect(got.input).toEqual(golden.input);
  });
});
