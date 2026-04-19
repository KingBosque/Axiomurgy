#!/usr/bin/env python3
"""
Offline advisory reasoning efficacy evaluation (plan JSON only).

Does not execute spells, change policy/fingerprints/Vermyth, or mutate spell files.
See docs/REASONING_EVAL_HARNESS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from axiomurgy.reasoning_eval.corpus import load_corpus, normalize_corpus_entries
from axiomurgy.reasoning_eval.labels import load_labels
from axiomurgy.reasoning_eval.metrics import aggregate_metrics, compute_cross_mode_metrics, human_agreement_metrics
from axiomurgy.reasoning_eval.modes import EVAL_MODES, all_mode_names
from axiomurgy.reasoning_eval.reports import write_report_artifacts
from axiomurgy.reasoning_eval.run import run_evaluation
from axiomurgy.util import ROOT


def _normalize_label_map(labels_path: Path) -> Dict[str, Dict[str, Any]]:
    raw = load_labels(labels_path)
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        p = Path(k)
        if p.is_absolute():
            key = str(p.resolve())
        else:
            key = str((ROOT / k).resolve())
        out[key] = v
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate advisory reasoning efficacy (plan-attached JSON only).")
    ap.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "corpus" / "reasoning_eval_corpus.json",
        help="Corpus JSON path (default: corpus/reasoning_eval_corpus.json)",
    )
    ap.add_argument(
        "--modes",
        type=str,
        default=",".join(all_mode_names()),
        help="Comma-separated mode names (default: all)",
    )
    ap.add_argument("--json", action="store_true", help="Print full JSON report to stdout")
    ap.add_argument(
        "--write-report",
        type=Path,
        metavar="PREFIX",
        help="Write PREFIX.json and PREFIX.md (e.g. artifacts/reasoning_eval/latest)",
    )
    ap.add_argument("--include-raw", action="store_true", help="Include raw plan JSON per row (large)")
    ap.add_argument("--labels", type=Path, help="Optional human labels JSON sidecar")
    ap.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="Base directory for isolated per-run artifact dirs (default: temp prefix)",
    )
    ap.add_argument("--limit", type=int, default=None, help="Only first N corpus spells")

    args = ap.parse_args(argv)

    doc_in = load_corpus(args.corpus.resolve())
    entries = normalize_corpus_entries(doc_in)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    for m in modes:
        if m not in EVAL_MODES:
            print(f"unknown mode: {m!r}; valid: {sorted(EVAL_MODES.keys())}", file=sys.stderr)
            return 2

    payload = run_evaluation(
        corpus_entries=entries,
        modes=modes,
        artifact_root=args.artifact_dir,
        limit=args.limit,
        include_raw_plan=args.include_raw,
    )

    by_mode_flat: Dict[str, List[Dict[str, Any]]] = {}
    for block in payload.get("modes") or []:
        by_mode_flat[str(block.get("mode"))] = list(block.get("results") or [])

    metrics_by_mode: Dict[str, Any] = {}
    for block in payload.get("modes") or []:
        mname = str(block.get("mode"))
        metrics_by_mode[mname] = aggregate_metrics(block.get("results") or [])

    payload["metrics_by_mode"] = metrics_by_mode
    payload["corpus_path"] = str(args.corpus.resolve())
    payload["modes_requested"] = modes
    payload["cross_mode_metrics"] = compute_cross_mode_metrics(by_mode_flat)

    labels_map: Dict[str, Dict[str, Any]] = {}
    if args.labels:
        labels_map = _normalize_label_map(args.labels.resolve())

    if labels_map:
        # Agreement on generation_ranked (Lullian visible) by default
        ranked = by_mode_flat.get("generation_ranked") or []
        payload["human_agreement"] = human_agreement_metrics(ranked, labels_map)

    out_doc: Dict[str, Any] = dict(payload)
    if args.json:
        print(json.dumps(out_doc, indent=2, ensure_ascii=False))

    if args.write_report:
        paths = write_report_artifacts(out_doc, args.write_report)
        print(f"Wrote {paths['json']}\n     {paths['markdown']}", file=sys.stderr)

    if not args.json and not args.write_report:
        print("Nothing to output: pass --json and/or --write-report PREFIX", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
