/**
 * Opt-in live Vermyth HTTP smoke (real endpoint).
 *
 * Enable with:
 *   AXIOMURGY_TS_VERMYTH_SMOKE=1
 *   AXIOMURGY_VERMYTH_BASE_URL or VERMYTH_BASE_URL
 *
 * Does not run in default CI; use locally or a manual workflow with secrets.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { VermythHttpClient } from "./client.js";
import { fetchHealthz } from "./healthz.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, "..", "..", "..", "..");

function smokeEnabled(): boolean {
  const v = process.env.AXIOMURGY_TS_VERMYTH_SMOKE ?? "";
  return ["1", "true", "yes"].includes(v.trim().toLowerCase());
}

function baseUrl(): string | null {
  const v = process.env.AXIOMURGY_VERMYTH_BASE_URL ?? process.env.VERMYTH_BASE_URL;
  if (typeof v === "string" && v.trim()) return v.trim().replace(/\/+$/, "");
  return null;
}

const runLiveSmoke = (): boolean => smokeEnabled() && Boolean(baseUrl());

const minimalIntent = {
  intent: {
    objective: "ts smoke intent",
    scope: "axiomurgy:ts_smoke",
    reversibility: "PARTIAL" as const,
    side_effect_tolerance: "MEDIUM" as const,
  },
};

describe("Vermyth HTTP integration (opt-in)", () => {
  it.skipIf(!runLiveSmoke())("healthz + arcane/recommend + tools/decide + tools/compile_program", async () => {
    const base = baseUrl()!;
    const hz = await fetchHealthz(base, { timeoutS: 10 });
    expect(hz).not.toBeNull();
    expect(hz!.status_code).toBeLessThan(500);

    const client = new VermythHttpClient(`${base}/`, { timeoutS: 15 });

    const rec = await client.arcaneRecommend({ skillId: "decide", input: minimalIntent });
    expect(typeof rec).toBe("object");
    expect(rec).not.toBeNull();

    const dec = await client.decide({
      intent: {
        objective: "test",
        scope: "axiomurgy",
        reversibility: "PARTIAL",
        side_effect_tolerance: "MEDIUM",
      },
      aspects: ["VOID", "FORM"],
    });
    expect(typeof dec).toBe("object");
    expect(dec).toHaveProperty("decision");

    const programPath = join(repoRoot, "docs", "fixtures", "ts-parity", "inbox_triage_semantic_program.json");
    const program = JSON.parse(readFileSync(programPath, "utf8")) as Record<string, unknown>;
    const compiled = await client.compileProgram(program);
    expect(typeof compiled).toBe("object");
    expect("validation" in compiled || "nodes" in compiled).toBe(true);
  });
});
