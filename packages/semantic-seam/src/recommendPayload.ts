/**
 * Mirrors axiomurgy.vermyth_export.spell_level_vermyth_intent + _recommend_input_payload.
 */

import { compilePlan, type SpellDocumentJson } from "./planner.js";

export type VermythIntentFields = {
  objective: string;
  scope: string;
  reversibility: "REVERSIBLE" | "PARTIAL" | "IRREVERSIBLE";
  side_effect_tolerance: "MEDIUM" | "HIGH";
};

export function spellLevelVermythIntent(doc: SpellDocumentJson): VermythIntentFields {
  const risk = String(doc.constraints?.risk ?? "low");
  const tol: "MEDIUM" | "HIGH" = risk === "high" || risk === "critical" ? "HIGH" : "MEDIUM";
  const plan = compilePlan(doc);
  const hasWrite = plan.some((s) => s.effect === "write");
  let rev: "REVERSIBLE" | "PARTIAL" | "IRREVERSIBLE";
  if (hasWrite) {
    rev = risk === "low" || risk === "medium" ? "PARTIAL" : "IRREVERSIBLE";
  } else {
    rev = "REVERSIBLE";
  }
  const summaryBits = [doc.spell, String(doc.intent ?? ""), risk];
  const inputText = summaryBits.join("\n").slice(0, 8000);
  const objective = inputText.slice(0, 500);
  const scope = `axiomurgy:${doc.spell}`.slice(0, 200);
  return {
    objective,
    scope,
    reversibility: rev,
    side_effect_tolerance: tol,
  };
}

export function recommendInputPayload(doc: SpellDocumentJson): { inputText: string; input: { intent: VermythIntentFields } } {
  const risk = String(doc.constraints?.risk ?? "low");
  const summaryBits = [doc.spell, String(doc.intent ?? ""), risk];
  const inputText = summaryBits.join("\n").slice(0, 8000);
  const intent = spellLevelVermythIntent(doc);
  return { inputText, input: { intent } };
}
