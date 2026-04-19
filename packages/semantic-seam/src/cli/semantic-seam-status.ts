#!/usr/bin/env node
/**
 * TypeScript counterpart to scripts/semantic_seam_status.py (read-only status JSON).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fetchHealthz } from "../healthz.js";
import { findRepoRoot } from "../repoRoot.js";

function envBaseUrl(): string | null {
  const v = process.env.AXIOMURGY_VERMYTH_BASE_URL ?? process.env.VERMYTH_BASE_URL;
  if (typeof v === "string" && v.trim()) return v.trim().replace(/\/+$/, "");
  return null;
}

function parseArgs(argv: string[]): {
  baseline: string;
  corpus: string;
  calibrationReport: string | null;
  probe: boolean;
  live: boolean;
} {
  let baseline = join("docs", "reports", "compatibility_baseline_live_v1.json");
  let corpus = join("docs", "data", "semantic_recommend_corpus.json");
  let calibrationReport: string | null = null;
  let probe = false;
  let live = false;
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--probe") probe = true;
    else if (a === "--live") live = true;
    else if (a === "--baseline" && argv[i + 1]) {
      baseline = argv[++i]!;
    } else if (a === "--corpus" && argv[i + 1]) {
      corpus = argv[++i]!;
    } else if (a === "--calibration-report" && argv[i + 1]) {
      calibrationReport = argv[++i]!;
    }
  }
  return { baseline, corpus, calibrationReport, probe, live };
}

async function main(): Promise<number> {
  const repoRoot = findRepoRoot(process.cwd());
  const args = parseArgs(process.argv);
  const baselinePath = join(repoRoot, args.baseline);

  const out: Record<string, unknown> = {
    kind: "semantic_seam_status",
    axiomurgy_repo_root: repoRoot,
  };

  try {
    readFileSync(baselinePath, "utf8");
  } catch {
    out.error = `baseline not found: ${baselinePath}`;
    console.log(JSON.stringify(out, null, 2));
    return 2;
  }

  const baseline = JSON.parse(readFileSync(baselinePath, "utf8")) as Record<string, unknown>;
  out.baseline_version = baseline.baseline_version;
  out.baseline_captured_at = baseline.captured_at;
  out.baseline_axiomurgy_git = baseline.axiomurgy_git;
  out.baseline_vermyth_git = baseline.vermyth_git;
  out.baseline_expectations_count = Array.isArray(baseline.expectations) ? baseline.expectations.length : 0;

  const corpusPath = join(repoRoot, args.corpus);
  try {
    const corpus = JSON.parse(readFileSync(corpusPath, "utf8")) as { version?: unknown; spells?: unknown[] };
    out.corpus_version = corpus.version;
    out.corpus_spell_count = Array.isArray(corpus.spells) ? corpus.spells.length : 0;
  } catch {
    /* optional */
  }

  if (args.calibrationReport) {
    const p = join(repoRoot, args.calibrationReport);
    try {
      const rep = JSON.parse(readFileSync(p, "utf8")) as { calibration?: { metrics?: { multi_match_rate?: unknown } } };
      const mm = rep.calibration?.metrics?.multi_match_rate;
      if (mm !== undefined && typeof mm === "object" && mm !== null) out.multi_match_rate = mm;
    } catch {
      /* ignore */
    }
  }

  if (args.probe || args.live) {
    const base = envBaseUrl();
    if (!base) {
      out.healthz = null;
      out.healthz_note = "AXIOMURGY_VERMYTH_BASE_URL not set; skipping probe";
    } else {
      const hz = await fetchHealthz(base, { timeoutS: 10 });
      out.healthz = hz;
      if (hz && typeof hz.status_code === "number") out.healthz_status_code = hz.status_code;
    }
  }

  console.log(JSON.stringify(out, null, 2));
  return 0;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(err);
    process.exit(1);
  },
);
