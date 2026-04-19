#!/usr/bin/env python3
"""Emit docs/fixtures/ts-parity/*.json for recommend input parity (run after changing spell/vermyth seam)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from axiomurgy.legacy import load_spell
from axiomurgy.vermyth_export import build_semantic_program
from axiomurgy.vermyth_integration import _recommend_input_payload

SPELLS = [
    "examples/primer_to_axioms.spell.json",
    "examples/inbox_triage.spell.json",
    "examples/openapi_ticket_then_fail.spell.json",
]


def main() -> int:
    out_dir = ROOT / "docs" / "fixtures" / "ts-parity"
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel in SPELLS:
        p = ROOT / rel
        spell = load_spell(p)
        input_text, payload = _recommend_input_payload(spell)
        stem = rel.replace("/", "__").replace(".spell.json", "")
        obj = {
            "spell_path": rel.replace("\\", "/"),
            "input_text": input_text,
            "input": payload,
        }
        (out_dir / f"{stem}.json").write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # TS opt-in Vermyth compile_program smoke (must match Python build_semantic_program)
    inbox = ROOT / "examples" / "inbox_triage.spell.json"
    spell_inbox = load_spell(inbox)
    prog = build_semantic_program(spell_inbox)
    (out_dir / "inbox_triage_semantic_program.json").write_text(
        json.dumps(prog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"Wrote {len(SPELLS)} recommend fixtures + inbox_triage_semantic_program.json under {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
