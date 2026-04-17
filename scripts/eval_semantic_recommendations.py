#!/usr/bin/env python3
"""Evaluate Axiomurgy -> Vermyth /arcane/recommend against real example spells (live server).

Uses the same probe payload as planning-time ``fetch_semantic_recommendations`` (decide skill_id).

Environment:
  AXIOMURGY_VERMYTH_BASE_URL or VERMYTH_BASE_URL — Vermyth HTTP base (required unless --offline)
  VERMYTH_HTTP_TOKEN / AXIOMURGY_VERMYTH_HTTP_TOKEN — optional Bearer auth

Examples:
  python scripts/eval_semantic_recommendations.py --json \\
    --corpus docs/data/semantic_recommend_corpus.json --write-report docs/reports/last_calibration

  python scripts/eval_semantic_recommendations.py --offline --corpus docs/data/semantic_recommend_corpus.json

  python scripts/eval_semantic_recommendations.py --write-baseline docs/reports/compatibility_baseline_live_v1.json

  python scripts/eval_semantic_recommendations.py --compare-baseline docs/reports/compatibility_baseline_live_v1.json --allow-sha-drift
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASELINE_VERSION = 1

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from axiomurgy.adapters.vermyth_http import VermythHttpClient, VermythHttpError
from axiomurgy.legacy import load_spell
from axiomurgy.vermyth_integration import _recommend_input_payload

DEFAULT_SPELLS = [
    "examples/inbox_triage.spell.json",
    "examples/openapi_ticket_then_fail.spell.json",
    "examples/research_brief.spell.json",
]

DEFAULT_CORPUS_REL = "docs/data/semantic_recommend_corpus.json"


def _git_head(repo: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _base_url() -> str | None:
    v = os.environ.get("AXIOMURGY_VERMYTH_BASE_URL") or os.environ.get("VERMYTH_BASE_URL")
    return v.strip().rstrip("/") if isinstance(v, str) and v.strip() else None


def load_corpus(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "spells" not in raw or not isinstance(raw["spells"], list):
        raise ValueError("corpus must contain a spells array")
    return raw


def expect_map_from_corpus(corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in corpus["spells"]:
        p = row.get("path")
        if isinstance(p, str):
            norm = p.replace("\\", "/")
            out[norm] = row
    return out


def _norm_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def normalize_recommendation_entry(r: dict[str, Any]) -> dict[str, Any]:
    """Same shape as ``run_probe`` normalized recommendations (stable for fingerprints)."""
    return {
        "bundle_id": r.get("bundle_id"),
        "version": r.get("version"),
        "match_kind": r.get("match_kind"),
        "strength": r.get("strength"),
        "target_skill": r.get("target_skill"),
    }


def fingerprint_from_normalized_recs(recs: list[dict[str, Any]]) -> str:
    norm = [normalize_recommendation_entry(r) for r in recs if isinstance(r, dict)]
    raw = json.dumps(norm, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def expectations_from_corpus(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn corpus ``spells`` into baseline expectation rows."""
    out: list[dict[str, Any]] = []
    for row in corpus["spells"]:
        p = str(row.get("path") or "").replace("\\", "/")
        ex = row.get("expect") or {}
        fam = row.get("family")
        must_in = list(ex.get("must_include_bundle_ids") or [])
        must_out = list(ex.get("must_not_include_bundle_ids") or [])
        primary = ex.get("primary_bundle_id")
        if fam == "negative_control":
            out.append(
                {
                    "spell_path": p,
                    "expected_top_bundle_id": None,
                    "expected_match_kind": None,
                    "forbidden_top_bundle_ids": must_out,
                    "recommendations_fingerprint": None,
                }
            )
        else:
            top = must_in[0] if must_in else primary
            out.append(
                {
                    "spell_path": p,
                    "expected_top_bundle_id": top,
                    "expected_match_kind": "exact",
                    "forbidden_top_bundle_ids": [],
                    "recommendations_fingerprint": None,
                }
            )
    return out


def merge_expectations_with_run_fingerprints(
    expectations: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_path = {str(r.get("spell_path") or "").replace("\\", "/"): r for r in runs}
    merged: list[dict[str, Any]] = []
    for exp in expectations:
        p = str(exp["spell_path"]).replace("\\", "/")
        run = by_path.get(p)
        e = dict(exp)
        if (
            run
            and not run.get("error")
            and isinstance(run.get("recommendations"), list)
        ):
            recs = [x for x in run["recommendations"] if isinstance(x, dict)]
            e["recommendations_fingerprint"] = fingerprint_from_normalized_recs(recs)
        merged.append(e)
    return merged


def build_baseline_payload(
    *,
    metadata: dict[str, Any],
    corpus: dict[str, Any],
    runs: list[dict[str, Any]],
    note: str | None = None,
) -> dict[str, Any]:
    exp = merge_expectations_with_run_fingerprints(expectations_from_corpus(corpus), runs)
    cap = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "baseline_version": BASELINE_VERSION,
        "captured_at": cap,
        "axiomurgy_git": metadata.get("axiomurgy_git"),
        "vermyth_git": metadata.get("vermyth_git"),
        "healthz": metadata.get("healthz"),
        "note": note
        or "HTTP baseline; refresh with: python scripts/eval_semantic_recommendations.py --calibrate --write-baseline PATH",
        "expectations": exp,
    }


def compare_to_baseline(
    baseline: dict[str, Any],
    *,
    current_meta: dict[str, Any],
    runs: list[dict[str, Any]],
    allow_sha_drift: bool,
) -> tuple[bool, list[str]]:
    """Return (ok, failure_messages). On regression, stderr should print the first spell-related failure."""
    failures: list[str] = []
    ag = baseline.get("axiomurgy_git")
    vg = baseline.get("vermyth_git")
    if not allow_sha_drift:
        if ag and current_meta.get("axiomurgy_git") != ag:
            failures.append(
                f"meta: axiomurgy_git drift baseline={ag!r} current={current_meta.get('axiomurgy_git')!r}"
            )
        if vg and current_meta.get("vermyth_git") != vg:
            failures.append(
                f"meta: vermyth_git drift baseline={vg!r} current={current_meta.get('vermyth_git')!r}"
            )

    by_path = {str(r.get("spell_path") or "").replace("\\", "/"): r for r in runs}

    for exp in baseline.get("expectations") or []:
        sp = str(exp.get("spell_path") or "").replace("\\", "/")
        run = by_path.get(sp)
        if not run:
            failures.append(f"{sp}: missing run in probe result")
            continue
        if run.get("error"):
            failures.append(f"{sp}: transport error: {run.get('error')}")
            continue

        recs = run.get("recommendations") if isinstance(run.get("recommendations"), list) else []
        recs_dicts = [x for x in recs if isinstance(x, dict)]
        exp_top = exp.get("expected_top_bundle_id")
        forbidden = list(exp.get("forbidden_top_bundle_ids") or [])

        if exp_top is None:
            if recs_dicts:
                tid = str(recs_dicts[0].get("bundle_id") or "")
                if tid in forbidden:
                    failures.append(
                        f"{sp}: negative control forbids top bundle {tid!r} (forbidden={forbidden})"
                    )
            fp_exp = exp.get("recommendations_fingerprint")
            if fp_exp is not None:
                got = fingerprint_from_normalized_recs(recs_dicts)
                if got != fp_exp:
                    failures.append(f"{sp}: recommendations_fingerprint mismatch (negative)")
        else:
            if not recs_dicts:
                failures.append(f"{sp}: expected top bundle {exp_top!r} but recommendations empty")
                continue
            top = recs_dicts[0]
            tid = str(top.get("bundle_id") or "")
            if tid != str(exp_top):
                failures.append(
                    f"{sp}: top bundle {tid!r} != baseline expected_top {str(exp_top)!r}"
                )
            emk = exp.get("expected_match_kind")
            if emk:
                mk = str(top.get("match_kind") or "")
                if mk != str(emk):
                    failures.append(
                        f"{sp}: match_kind {mk!r} != baseline expected {str(emk)!r}"
                    )
            fp_exp = exp.get("recommendations_fingerprint")
            if fp_exp:
                got = fingerprint_from_normalized_recs(recs_dicts)
                if got != fp_exp:
                    failures.append(f"{sp}: recommendations_fingerprint mismatch")

    return (len(failures) == 0, failures)


def _heuristic_miss_reasons(
    raw: dict[str, Any],
    *,
    skill_id: str,
    recs: list[Any],
) -> list[str]:
    out: list[str] = []
    if recs:
        return out
    if raw.get("note"):
        out.append(f"note: {raw['note']}")
    out.append(
        "zero recommendations: check target_skills includes this skill_id "
        f"({skill_id!r}), intent_subset_eq vs spell_level_vermyth_intent fields, "
        "and default min_strength (0.55) vs advisory tier strength."
    )
    return out


def run_probe(
    spell_paths: list[Path],
    *,
    base_url: str,
    skill_id: str,
    min_strength: float | None,
    timeout_s: float,
    raw_by_path: bool = False,
) -> dict[str, Any]:
    client = VermythHttpClient(base_url + "/", timeout_s=timeout_s)
    runs: list[dict[str, Any]] = []
    for path in spell_paths:
        rel = _norm_rel(path, ROOT)
        spell = load_spell(path)
        _text, input_payload = _recommend_input_payload(spell)
        try:
            ip = input_payload
            raw, latency_ms = VermythHttpClient.timed_call(
                lambda ip=ip: client.arcane_recommend(
                    skill_id=skill_id,
                    input_=ip,
                    min_strength=min_strength,
                )
            )
        except VermythHttpError as exc:
            runs.append(
                {
                    "spell_path": rel,
                    "spell_name": spell.name,
                    "error": str(exc),
                    "latency_ms": None,
                }
            )
            continue
        recs = raw.get("recommendations") if isinstance(raw.get("recommendations"), list) else []
        normalized: list[dict[str, Any]] = []
        for r in recs:
            if not isinstance(r, dict):
                continue
            normalized.append(
                {
                    "bundle_id": r.get("bundle_id"),
                    "version": r.get("version"),
                    "match_kind": r.get("match_kind"),
                    "strength": r.get("strength"),
                    "target_skill": r.get("target_skill"),
                }
            )
        row: dict[str, Any] = {
            "spell_path": rel,
            "spell_name": spell.name,
            "latency_ms": round(latency_ms, 3),
            "recommendation_count": len(recs),
            "recommendations": normalized,
            "raw_skill_id_echo": raw.get("skill_id"),
            "likely_miss_reasons_if_empty": _heuristic_miss_reasons(raw, skill_id=skill_id, recs=recs),
        }
        if raw_by_path:
            row["raw_response"] = raw
        runs.append(row)

    return {"runs": runs, "skill_id": skill_id, "min_strength": min_strength}


def fetch_healthz(base_url: str, *, timeout_s: float = 5.0) -> dict[str, Any] | None:
    try:
        import requests

        url = base_url.rstrip("/") + "/healthz"
        r = requests.get(url, timeout=timeout_s)
        try:
            body = r.json()
        except ValueError:
            body = {"text": r.text[:500]}
        return {"status_code": r.status_code, "body": body}
    except OSError:
        return None


def classify_row(
    run: dict[str, Any],
    expect: dict[str, Any] | None,
) -> str:
    """Return calibration label: correct_match | weak_but_plausible | wrong_match | no_match | error | unknown."""
    if run.get("error"):
        return "error"
    if expect is None:
        return "unknown"
    ex = expect.get("expect") or {}
    must_in = list(ex.get("must_include_bundle_ids") or [])
    must_out = list(ex.get("must_not_include_bundle_ids") or [])
    primary = ex.get("primary_bundle_id")
    recs = run.get("recommendations") if isinstance(run.get("recommendations"), list) else []
    if not recs:
        return "no_match"
    top = recs[0]
    tid = str(top.get("bundle_id") or "")
    mk = str(top.get("match_kind") or "")

    if must_out and tid in must_out:
        return "wrong_match"

    if not must_in:
        return "correct_match"

    if tid not in must_in:
        return "wrong_match"

    if mk == "exact":
        return "correct_match"
    return "weak_but_plausible"


def rollup_calibration(
    runs: list[dict[str, Any]],
    corpus: dict[str, Any] | None,
) -> dict[str, Any]:
    expect_by_path: dict[str, dict[str, Any]] = expect_map_from_corpus(corpus) if corpus else {}
    labeled: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for run in runs:
        rel = run.get("spell_path") or ""
        rel_n = rel.replace("\\", "/")
        row_corpus = expect_by_path.get(rel_n)
        fam = row_corpus.get("family", "unknown") if row_corpus else "unknown"
        label = classify_row(run, row_corpus)
        counts[label] += 1
        by_family[fam][label] += 1
        ext = dict(run)
        ext["calibration_label"] = label
        ext["family"] = fam
        labeled.append(ext)

    positives = [r for r in labeled if r.get("family") != "negative_control"]
    neg = [r for r in labeled if r.get("family") == "negative_control"]
    pos_ok = sum(
        1 for r in positives if r["calibration_label"] in ("correct_match", "weak_but_plausible")
    )
    neg_pass = sum(1 for r in neg if r["calibration_label"] != "wrong_match")
    multi = sum(1 for r in labeled if (r.get("recommendation_count") or 0) > 1)

    return {
        "labeled_runs": labeled,
        "counts_by_label": dict(counts),
        "counts_by_family": {k: dict(v) for k, v in by_family.items()},
        "metrics": {
            "positive_correct_or_weak": {"numerator": pos_ok, "denominator": len(positives)},
            "negative_controls_no_false_positive": {"numerator": neg_pass, "denominator": len(neg)},
            "empty_recommendations": sum(1 for r in labeled if r["calibration_label"] == "no_match"),
            "multi_match_rate": {
                "numerator": multi,
                "denominator": len(labeled),
            },
        },
    }


def write_markdown_summary(path: Path, report: dict[str, Any], calibration: dict[str, Any]) -> None:
    lines = [
        "# Semantic recommendation calibration report",
        "",
        f"- axiomurgy_git: `{report.get('metadata', {}).get('axiomurgy_git')}`",
        f"- vermyth_git: `{report.get('metadata', {}).get('vermyth_git')}`",
        f"- healthz: `{json.dumps(report.get('metadata', {}).get('healthz'), indent=None)}`",
        "",
        "## Counts by label",
        "",
    ]
    for k, v in sorted(calibration.get("counts_by_label", {}).items()):
        lines.append(f"- **{k}**: {v}")
    lines.extend(["", "## By family", ""])
    for fam, d in sorted(calibration.get("counts_by_family", {}).items()):
        lines.append(f"### {fam}")
        for kk, vv in sorted(d.items()):
            lines.append(f"- {kk}: {vv}")
        lines.append("")
    m = calibration.get("metrics", {})
    lines.extend(
        [
            "## Metrics",
            "",
            "```json",
            json.dumps(m, indent=2),
            "```",
            "",
            "## Per spell",
            "",
        ]
    )
    for r in calibration.get("labeled_runs", []):
        lines.append(
            f"- `{r.get('spell_path')}` — **{r.get('calibration_label')}** "
            f"(top-1: `{r.get('recommendations') and r['recommendations'][0].get('bundle_id')}`)"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate Vermyth semantic recommendations for Axiomurgy spells.")
    ap.add_argument("--spells", nargs="*", default=None, help="Spell paths relative to repo root (default: three examples or full corpus).")
    ap.add_argument(
        "--corpus",
        default=None,
        help=f"Corpus JSON with expectations (default path if --calibrate only: {DEFAULT_CORPUS_REL}).",
    )
    ap.add_argument(
        "--calibrate",
        action="store_true",
        help="Load corpus expectations and add calibration labels + metrics (use with --corpus or default corpus file).",
    )
    ap.add_argument(
        "--write-report",
        default=None,
        metavar="PREFIX",
        help="Write PREFIX.json and PREFIX.md (no extension).",
    )
    ap.add_argument("--base-url", default=None, help="Override AXIOMURGY_VERMYTH_BASE_URL / VERMYTH_BASE_URL")
    ap.add_argument("--skill-id", default="decide", help="Vermyth recommendation target (default: decide).")
    ap.add_argument(
        "--min-strength",
        type=float,
        default=None,
        help="Optional min_strength filter (Vermyth default 0.55 if omitted on server).",
    )
    ap.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout seconds.")
    ap.add_argument("--json", action="store_true", help="Print JSON report only.")
    ap.add_argument(
        "--offline",
        action="store_true",
        help="Print probe shapes only (no HTTP); includes heuristic pin metadata.",
    )
    ap.add_argument("--include-raw", action="store_true", help="Include raw Vermyth JSON per spell in live report.")
    ap.add_argument(
        "--write-baseline",
        default=None,
        metavar="PATH",
        help="After a live probe against the corpus spell list, write a v1 compatibility_baseline JSON (for committing).",
    )
    ap.add_argument(
        "--compare-baseline",
        default=None,
        metavar="PATH",
        help="After a live probe, compare to a committed baseline; exit 1 on regression (stderr: first failure).",
    )
    ap.add_argument(
        "--allow-sha-drift",
        action="store_true",
        help="With --compare-baseline, do not fail when axiomurgy_git / vermyth_git differ from the baseline.",
    )
    args = ap.parse_args()

    if args.offline and (args.write_baseline or args.compare_baseline):
        print("error: --write-baseline and --compare-baseline require live HTTP", file=sys.stderr)
        return 2

    if args.corpus:
        corpus_path: Path | None = Path(args.corpus)
    elif args.calibrate or args.write_baseline or args.compare_baseline:
        corpus_path = ROOT / DEFAULT_CORPUS_REL
    else:
        corpus_path = None

    corpus = None
    if corpus_path is not None:
        if not corpus_path.is_file():
            print(f"error: corpus file missing: {corpus_path}", file=sys.stderr)
            return 2
        corpus = load_corpus(corpus_path)

    if args.spells:
        rel_spells = args.spells
    elif corpus:
        rel_spells = [str(x["path"]) for x in corpus["spells"]]
    else:
        rel_spells = DEFAULT_SPELLS

    spell_paths = [(ROOT / p).resolve() for p in rel_spells]
    for p in spell_paths:
        if not p.is_file():
            print(f"error: missing spell file: {p}", file=sys.stderr)
            return 2

    meta: dict[str, Any] = {
        "axiomurgy_git": _git_head(ROOT),
        "vermyth_git": _git_head(ROOT.parent / "Vermyth") if (ROOT.parent / "Vermyth").is_dir() else None,
        "environment_note": "Pin Vermyth version in CI/docs when recording golden runs",
    }

    if args.offline:
        rows: list[dict[str, Any]] = []
        for path in spell_paths:
            spell = load_spell(path)
            _t, payload = _recommend_input_payload(spell)
            rows.append(
                {
                    "spell_name": spell.name,
                    "spell_path": _norm_rel(path, ROOT),
                    "input": payload,
                }
            )
        out: dict[str, Any] = {"metadata": meta, "offline_probe_inputs": rows, "skill_id": args.skill_id}
        print(json.dumps(out, indent=2))
        return 0

    base = args.base_url or _base_url()
    if not base:
        print(
            "error: set AXIOMURGY_VERMYTH_BASE_URL or pass --base-url (or use --offline)",
            file=sys.stderr,
        )
        return 2

    meta["healthz"] = fetch_healthz(base, timeout_s=min(args.timeout, 10.0))

    probe_result = run_probe(
        spell_paths,
        base_url=base,
        skill_id=args.skill_id,
        min_strength=args.min_strength,
        timeout_s=args.timeout,
        raw_by_path=args.include_raw,
    )

    report: dict[str, Any] = {
        "metadata": meta,
        "corpus_path": str(corpus_path.resolve()) if corpus_path is not None else None,
        "report": probe_result,
    }

    if args.calibrate and corpus:
        report["calibration"] = rollup_calibration(probe_result["runs"], corpus)

    out_obj = report
    if args.json:
        print(json.dumps(out_obj, indent=2))
    else:
        print(json.dumps(out_obj, indent=2))
        print(
            "\n--- pin ---\n"
            f"axiomurgy_git={meta.get('axiomurgy_git')}\n"
            f"vermyth_git={meta.get('vermyth_git')}",
            file=sys.stderr,
        )

    if args.write_report:
        prefix = Path(args.write_report)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        json_path = prefix.with_suffix(".json")
        md_path = prefix.with_suffix(".md")
        json_path.write_text(json.dumps(out_obj, indent=2), encoding="utf-8")
        if args.calibrate and corpus and "calibration" in report:
            write_markdown_summary(md_path, report, report["calibration"])
        else:
            md_path.write_text(f"# Report\n\nSee {json_path.name}\n", encoding="utf-8")

    exit_code = 0
    if args.write_baseline:
        if corpus is None:
            print(
                "error: --write-baseline requires corpus expectations (use --corpus or rely on default file)",
                file=sys.stderr,
            )
            return 2
        bl = build_baseline_payload(metadata=meta, corpus=corpus, runs=probe_result["runs"])
        wpath = Path(args.write_baseline)
        wpath.parent.mkdir(parents=True, exist_ok=True)
        wpath.write_text(json.dumps(bl, indent=2) + "\n", encoding="utf-8")
    if args.compare_baseline:
        bpath = Path(args.compare_baseline)
        if not bpath.is_file():
            print(f"error: baseline file not found: {bpath}", file=sys.stderr)
            return 2
        baseline_obj = json.loads(bpath.read_text(encoding="utf-8"))
        ok, fails = compare_to_baseline(
            baseline_obj,
            current_meta=meta,
            runs=probe_result["runs"],
            allow_sha_drift=args.allow_sha_drift,
        )
        if not ok:
            if fails:
                print(fails[0], file=sys.stderr)
                for line in fails[1:]:
                    print(line, file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
