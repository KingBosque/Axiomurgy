"""JSON + Markdown report builders for reasoning efficacy evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)


def build_markdown_report(doc: Mapping[str, Any]) -> str:
    """Human-readable summary: metrics table, per-family notes, no-candidate highlights."""
    lines: List[str] = [
        "# Reasoning efficacy evaluation",
        "",
        "Advisory-only: measures plan-attached **reasoning** payloads, not execution success.",
        "",
        f"Harness version: `{doc.get('eval_harness_version', '')}`",
        "",
    ]
    modes = doc.get("modes") or []
    metrics_by_mode = doc.get("metrics_by_mode") or {}
    cross = doc.get("cross_mode_metrics") or {}

    lines.append("## Metrics by mode")
    lines.append("")
    for block in modes:
        mname = block.get("mode", "")
        m = metrics_by_mode.get(mname, {})
        lines.append(f"### `{mname}`")
        lines.append("")
        rows = [(k, m.get(k)) for k in sorted(m.keys()) if k != "candidate_kind_distribution"]
        lines.append(_md_table(["metric", "value"], rows))
        lines.append("")
        ckd = m.get("candidate_kind_distribution") or {}
        if ckd:
            lines.append("Candidate kind counts (summed across spells):")
            lines.append("")
            lines.append(_md_table(["kind", "count"], sorted(ckd.items())))
            lines.append("")

    if cross:
        lines.append("## Cross-mode signals")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(cross, indent=2))
        lines.append("```")
        lines.append("")

    lines.append("## No-candidate cases (all modes)")
    lines.append("")
    for block in modes:
        mode = block.get("mode", "")
        for r in block.get("results") or []:
            if int(r.get("candidate_count") or 0) == 0 and mode in (
                "generation_only",
                "generation_ranked",
                "generation_ranked_wyrd",
            ):
                lines.append(
                    f"- `{mode}` **{r.get('spell_name')}** ({r.get('family')}) — shape `{r.get('output_shape')}`"
                )
    lines.append("")

    lines.append("## Ranking notes (Lullian modes)")
    lines.append("")
    for block in modes:
        if block.get("mode") not in ("generation_ranked", "generation_ranked_wyrd"):
            continue
        for r in block.get("results") or []:
            vs = r.get("verification_statuses") or []
            if len(vs) > 1 and vs[0] == vs[1]:
                lines.append(f"- `{r.get('spell_name')}`: tied top statuses {vs[:2]}")
    lines.append("")

    hag = doc.get("human_agreement")
    if hag:
        lines.append("## Human label agreement (optional)")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(hag, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def write_report_artifacts(doc: Mapping[str, Any], prefix: Path) -> Dict[str, str]:
    """Write ``prefix.json`` and ``prefix.md``; return written paths."""
    prefix.parent.mkdir(parents=True, exist_ok=True)
    js = prefix.with_suffix(".json")
    md = prefix.with_suffix(".md")
    js.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md.write_text(build_markdown_report(doc), encoding="utf-8")
    return {"json": str(js.resolve()), "markdown": str(md.resolve())}
