#!/usr/bin/env python3
"""Print a Markdown matrix of Vermyth arcane bundles that expose ``recommendation`` (read-only).

Walks ``Vermyth/vermyth/data/arcane/bundles/*.json`` (or ``--bundles-dir``) and, for each bundle
with a ``recommendation`` block, summarizes ``target_skills``, whether tiers use ``aspects_eq`` /
``thresholds_eq``, and whether any ``intent_subset_eq`` value uses ``scope: semantic_bundle``
(vs axiomurgy intent scopes).

Examples:

  python scripts/dump_bundle_recommend_matrix.py
  python scripts/dump_bundle_recommend_matrix.py --bundles-dir ../Vermyth/vermyth/data/arcane/bundles
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _tier_ops(tiers: list[Any]) -> set[str]:
    ops: set[str] = set()
    for tier in tiers if isinstance(tiers, list) else []:
        if not isinstance(tier, dict):
            continue
        reqs = tier.get("require_all") if isinstance(tier.get("require_all"), list) else []
        for rule in reqs:
            if isinstance(rule, dict) and isinstance(rule.get("op"), str):
                ops.add(rule["op"])
    return ops


def _semantic_bundle_scope_any(tiers: list[Any]) -> bool:
    for tier in tiers if isinstance(tiers, list) else []:
        if not isinstance(tier, dict):
            continue
        reqs = tier.get("require_all") if isinstance(tier.get("require_all"), list) else []
        for rule in reqs:
            if not isinstance(rule, dict):
                continue
            if rule.get("op") != "intent_subset_eq":
                continue
            val = rule.get("value")
            if isinstance(val, dict) and val.get("scope") == "semantic_bundle":
                return True
    return False


def analyze_bundle(data: dict[str, Any]) -> dict[str, Any] | None:
    rec = data.get("recommendation")
    if not isinstance(rec, dict):
        return None
    bid = data.get("id")
    tiers = rec.get("tiers") if isinstance(rec.get("tiers"), list) else []
    sk = rec.get("target_skills")
    skills = sk if isinstance(sk, list) else []
    ops = _tier_ops(tiers)
    return {
        "bundle_id": bid,
        "target_skills": ",".join(str(s) for s in skills),
        "has_aspects_eq": "aspects_eq" in ops,
        "has_thresholds_eq": "thresholds_eq" in ops,
        "semantic_bundle_scope_in_intent": _semantic_bundle_scope_any(tiers),
        "tier_ops": ",".join(sorted(ops)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump Vermyth recommendation bundle matrix (Markdown).")
    here = Path(__file__).resolve().parent
    root = here.parent
    default_bundles = root.parent / "Vermyth" / "vermyth" / "data" / "arcane" / "bundles"
    ap.add_argument(
        "--bundles-dir",
        type=Path,
        default=default_bundles,
        help=f"Directory of bundle JSON files (default: {default_bundles})",
    )
    args = ap.parse_args()
    d = args.bundles_dir
    if not d.is_dir():
        print(f"error: bundles directory not found: {d}", flush=True)
        return 2

    rows: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        a = analyze_bundle(data)
        if a is not None:
            rows.append(a)

    print("# Vermyth decide bundles with `recommendation`\n")
    print(
        "| bundle_id | target_skills | aspects_eq | thresholds_eq | scope semantic_bundle | tier ops |"
    )
    print("|-----------|---------------|------------|---------------|----------------------|----------|")
    for r in rows:
        print(
            f"| `{r['bundle_id']}` | `{r['target_skills']}` | "
            f"{str(r['has_aspects_eq']).lower()} | {str(r['has_thresholds_eq']).lower()} | "
            f"{str(r['semantic_bundle_scope_in_intent']).lower()} | `{r['tier_ops']}` |"
        )
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
