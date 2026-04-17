"""Vermyth export, attestation allowlist, and optional integration stubs."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from axiomurgy.legacy import Spell, Step, load_json, load_spell
from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.review import _attestation_allowlisted_path
from axiomurgy.vermyth_export import VERMYTH_PROGRAM_EXPORT_VERSION, build_semantic_program, build_vermyth_program_export

ROOT = Path(__file__).resolve().parents[1]


class TestVermythExport(unittest.TestCase):
    def test_semantic_program_deterministic(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        spell = load_spell(spell_path)
        a = json.dumps(build_semantic_program(spell), sort_keys=True)
        b = json.dumps(build_semantic_program(spell), sort_keys=True)
        self.assertEqual(a, b)
        self.assertIn("vermyth_program_export_version", json.dumps({"x": build_vermyth_program_export(spell)}))

    def test_attestation_allowlist_paths(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.semantic_recommendations"))
        self.assertTrue(_attestation_allowlisted_path("plan.vermyth_program_export.version"))
        self.assertTrue(_attestation_allowlisted_path("describe.culture.records"))

    @patch("axiomurgy.vermyth_integration.fetch_semantic_recommendations")
    @patch("axiomurgy.vermyth_integration.compile_program_preview")
    def test_plan_enrichment_flags(self, mock_compile, mock_rec) -> None:
        mock_rec.return_value = {"status": "ok", "items": []}
        mock_compile.return_value = {"status": "ok", "validation": {"ok": True}}
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        resolved = resolve_run_target(spell_path, None, policy_path, ROOT / "artifacts")
        out = build_plan_summary(
            resolved,
            approvals=set(),
            vermyth_program=True,
            vermyth_validate=True,
            vermyth_recommendations=True,
        )
        self.assertEqual(out["vermyth_program_export"]["vermyth_program_export_version"], VERMYTH_PROGRAM_EXPORT_VERSION)
        self.assertIn("semantic_recommendations", out)


class TestVermythGateConfig(unittest.TestCase):
    def test_gate_skipped_when_disabled(self) -> None:
        from axiomurgy.vermyth_integration import run_vermyth_gate

        spell = Spell(
            name="t",
            intent="i",
            inputs={},
            constraints={},
            graph=[Step(step_id="s1", rune="mirror.read", effect="read", args={"input": "x"})],
            rollback=[],
            witness={"record": False},
            source_path=Path("x.spell.json"),
        )
        policy = load_json(ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json")
        r = run_vermyth_gate(spell, policy)
        self.assertEqual(r.get("status"), "skipped")
