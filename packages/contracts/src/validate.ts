import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import type { ErrorObject } from "ajv";
import {
  readCompatibilityBaselineSchema,
  readSpellbookSchema,
  readSpellSchema,
} from "./schemas.js";

export type ValidateResult = { ok: true } | { ok: false; errors: ErrorObject[] | null | undefined };

function makeValidator(schema: object) {
  const ajv = new Ajv2020({ allErrors: true, strict: false });
  addFormats(ajv);
  return ajv.compile(schema);
}

const validateSpell = makeValidator(readSpellSchema());
const validateSpellbook = makeValidator(readSpellbookSchema());
const validateBaseline = makeValidator(readCompatibilityBaselineSchema());

export function validateSpellJson(data: unknown): ValidateResult {
  const ok = validateSpell(data);
  if (ok) return { ok: true };
  return { ok: false, errors: validateSpell.errors };
}

export function validateSpellbookJson(data: unknown): ValidateResult {
  const ok = validateSpellbook(data);
  if (ok) return { ok: true };
  return { ok: false, errors: validateSpellbook.errors };
}

export function validateCompatibilityBaselineJson(data: unknown): ValidateResult {
  const ok = validateBaseline(data);
  if (ok) return { ok: true };
  return { ok: false, errors: validateBaseline.errors };
}
