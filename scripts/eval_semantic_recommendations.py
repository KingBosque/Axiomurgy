#!/usr/bin/env python3
"""Evaluate Axiomurgy -> Vermyth /arcane/recommend against real example spells (live server).

Uses the same probe payload as planning-time ``fetch_semantic_recommendations`` (decide skill_id).

Environment:
  AXIOMURGY_VERMYTH_BASE_URL or VERMYTH_BASE_URL — Vermyth HTTP base (required unless --offline)
  VERMYTH_HTTP_TOKEN / AXIOMURGY_VERMYTH_HTTP_TOKEN — optional Bearer auth

Example:
  set VERMYTH_HTTP_TOKEN=...
  set AXIOMURGY_VERMYTH_BASE_URL=http://127.0.0.1:7777
  python scripts/eval_semantic_recommendations.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    # Vermyth does not expose per-bundle failure reasons
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
) -> dict[str, Any]:
    client = VermythHttpClient(base_url + "/", timeout_s=timeout_s)
    runs: list[dict[str, Any]] = []
    for path in spell_paths:
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
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
        runs.append(
            {
                "spell_path": rel,
                "spell_name": spell.name,
                "latency_ms": round(latency_ms, 3),
                "recommendation_count": len(recs),
                "recommendations": normalized,
                "raw_skill_id_echo": raw.get("skill_id"),
                "likely_miss_reasons_if_empty": _heuristic_miss_reasons(raw, skill_id=skill_id, recs=recs),
            }
        )

    return {"runs": runs, "skill_id": skill_id, "min_strength": min_strength}


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate Vermyth semantic recommendations for Axiomurgy spells.")
    ap.add_argument(
        "--spells",
        nargs="*",
        default=DEFAULT_SPELLS,
        help="Spell paths relative to repo root (default: three canonical examples).",
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
    args = ap.parse_args()

    spell_paths = [(ROOT / p).resolve() for p in args.spells]
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
                    "spell_path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                    "input": payload,
                }
            )
        out = {"metadata": meta, "offline_probe_inputs": rows, "skill_id": args.skill_id}
        print(json.dumps(out, indent=2))
        return 0

    base = args.base_url or _base_url()
    if not base:
        print(
            "error: set AXIOMURGY_VERMYTH_BASE_URL or pass --base-url (or use --offline)",
            file=sys.stderr,
        )
        return 2

    report: dict[str, Any] = {
        "metadata": meta,
        "report": run_probe(
            spell_paths,
            base_url=base,
            skill_id=args.skill_id,
            min_strength=args.min_strength,
            timeout_s=args.timeout,
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(json.dumps(report, indent=2))
    print(
        "\n--- pin ---\n"
        f"axiomurgy_git={meta.get('axiomurgy_git')}\n"
        f"vermyth_git={meta.get('vermyth_git')}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
