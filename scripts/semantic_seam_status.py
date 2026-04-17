#!/usr/bin/env python3
"""Print a JSON status object for the Axiomurgy ↔ Vermyth semantic recommendation seam (read-only).

Reads the committed baseline and corpus; optionally probes HTTP healthz (--probe) or attaches
multi_match_rate from a prior --calibrate JSON report (--calibration-report).

Environment (optional):
  AXIOMURGY_VERMYTH_BASE_URL or VERMYTH_BASE_URL — for --probe / --live

Examples:
  python scripts/semantic_seam_status.py
  python scripts/semantic_seam_status.py --probe
  python scripts/semantic_seam_status.py --calibration-report docs/reports/last_run.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_eval_module():
    path = ROOT / "scripts" / "eval_semantic_recommendations.py"
    spec = importlib.util.spec_from_file_location("eval_semantic_recommendations", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _base_url() -> str | None:
    v = os.environ.get("AXIOMURGY_VERMYTH_BASE_URL") or os.environ.get("VERMYTH_BASE_URL")
    return v.strip().rstrip("/") if isinstance(v, str) and v.strip() else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Semantic seam status JSON (baseline + corpus + optional probe).")
    ap.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "docs" / "reports" / "compatibility_baseline_live_v1.json",
        help="Committed compatibility baseline JSON",
    )
    ap.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "docs" / "data" / "semantic_recommend_corpus.json",
        help="Semantic recommend corpus JSON",
    )
    ap.add_argument(
        "--calibration-report",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional prior harness JSON (with calibration.metrics) for multi_match_rate",
    )
    ap.add_argument(
        "--probe",
        action="store_true",
        help="If AXIOMURGY_VERMYTH_BASE_URL is set, GET /healthz (cheap; no recommend calls)",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="With --probe, same as --probe (reserved for future full probe; currently identical)",
    )
    args = ap.parse_args()
    _ = args.live  # reserved

    out: dict[str, Any] = {"kind": "semantic_seam_status", "axiomurgy_repo_root": str(ROOT)}

    if not args.baseline.is_file():
        out["error"] = f"baseline not found: {args.baseline}"
        print(json.dumps(out, indent=2))
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8-sig"))
    out["baseline_version"] = baseline.get("baseline_version")
    out["baseline_captured_at"] = baseline.get("captured_at")
    out["baseline_axiomurgy_git"] = baseline.get("axiomurgy_git")
    out["baseline_vermyth_git"] = baseline.get("vermyth_git")
    out["baseline_expectations_count"] = len(baseline.get("expectations") or [])

    if args.corpus.is_file():
        corpus = json.loads(args.corpus.read_text(encoding="utf-8-sig"))
        out["corpus_version"] = corpus.get("version")
        out["corpus_spell_count"] = len(corpus.get("spells") or [])

    if args.calibration_report and args.calibration_report.is_file():
        rep = json.loads(args.calibration_report.read_text(encoding="utf-8-sig"))
        cal = rep.get("calibration") or {}
        metrics = cal.get("metrics") or {}
        mm = metrics.get("multi_match_rate")
        if isinstance(mm, dict):
            out["multi_match_rate"] = mm

    if args.probe or args.live:
        base = _base_url()
        if not base:
            out["healthz"] = None
            out["healthz_note"] = "AXIOMURGY_VERMYTH_BASE_URL not set; skipping probe"
        else:
            mod = _load_eval_module()
            hz = mod.fetch_healthz(base, timeout_s=10.0)
            out["healthz"] = hz
            if isinstance(hz, dict) and isinstance(hz.get("status_code"), int):
                out["healthz_status_code"] = hz["status_code"]

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
