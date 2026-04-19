#!/usr/bin/env node
/**
 * TypeScript counterpart to scripts/eval_semantic_recommendations.py --offline (probe shapes only).
 */
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { gitHead } from "../gitMeta.js";
import { recommendInputPayload } from "../recommendPayload.js";
import type { SpellDocumentJson } from "../planner.js";
import { findRepoRoot } from "../repoRoot.js";

const DEFAULT_SPELLS = [
  "examples/inbox_triage.spell.json",
  "examples/openapi_ticket_then_fail.spell.json",
  "examples/research_brief.spell.json",
];

function parseArgs(argv: string[]): {
  offline: boolean;
  corpus: string | null;
  spells: string[] | null;
  skillId: string;
} {
  let offline = false;
  let corpus: string | null = null;
  let spells: string[] | null = null;
  let skillId = "decide";
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--offline") offline = true;
    else if (a === "--corpus" && argv[i + 1]) corpus = argv[++i]!;
    else if (a === "--spells") {
      spells = [];
      while (i + 1 < argv.length && argv[i + 1] && !argv[i + 1]!.startsWith("-")) spells.push(argv[++i]!);
    } else if (a === "--skill-id" && argv[i + 1]) skillId = argv[++i]!;
  }
  return { offline, corpus, spells, skillId };
}

async function main(): Promise<number> {
  const repoRoot = findRepoRoot(process.cwd());
  const args = parseArgs(process.argv);

  if (!args.offline) {
    console.error(
      "error: TypeScript CLI supports --offline only (use Python eval_semantic_recommendations.py for live HTTP)",
    );
    return 2;
  }

  let relSpells: string[];
  if (args.spells && args.spells.length > 0) {
    relSpells = args.spells;
  } else if (args.corpus) {
    const corpusPath = join(repoRoot, args.corpus);
    if (!existsSync(corpusPath)) {
      console.error(`error: corpus file missing: ${corpusPath}`);
      return 2;
    }
    const corpus = JSON.parse(readFileSync(corpusPath, "utf8")) as { spells: { path: string }[] };
    relSpells = corpus.spells.map((r) => r.path.replace(/\\/g, "/"));
  } else {
    relSpells = DEFAULT_SPELLS;
  }

  const spellPaths = relSpells.map((p) => join(repoRoot, ...p.split("/")));
  for (const p of spellPaths) {
    if (!existsSync(p)) {
      console.error(`error: missing spell file: ${p}`);
      return 2;
    }
  }

  const meta = {
    axiomurgy_git: gitHead(repoRoot),
    vermyth_git: null as string | null,
    environment_note: "Pin Vermyth version in CI/docs when recording golden runs",
  };

  const rows: { spell_name: string; spell_path: string; input: Record<string, unknown> }[] = [];
  for (let i = 0; i < spellPaths.length; i++) {
    const path = spellPaths[i]!;
    const doc = JSON.parse(readFileSync(path, "utf8")) as SpellDocumentJson;
    const payload = recommendInputPayload(doc);
    const rel = relSpells[i]!.replace(/\\/g, "/");
    rows.push({
      spell_name: doc.spell,
      spell_path: rel,
      input: payload.input as Record<string, unknown>,
    });
  }

  const out = {
    metadata: meta,
    offline_probe_inputs: rows,
    skill_id: args.skillId,
  };
  console.log(JSON.stringify(out, null, 2));
  return 0;
}

main().then(
  (c) => process.exit(c),
  (e) => {
    console.error(e);
    process.exit(1);
  },
);
