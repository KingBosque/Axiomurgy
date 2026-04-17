"""Vermyth export, attestation allowlist, and optional integration stubs."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from axiomurgy.legacy import Spell, Step, load_json, load_spell
from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.review import _attestation_allowlisted_path, compare_reviewed_bundle
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

    def test_allowlist_prefix_and_non_matching_paths(self) -> None:
        # Prefix policy: anything under plan.semantic_recommendations* is skipped (see VERMYTH_GATE.md).
        self.assertTrue(_attestation_allowlisted_path("plan.semantic_recommendations_extra"))
        self.assertFalse(_attestation_allowlisted_path("describe.semantic_recommendations"))
        self.assertFalse(_attestation_allowlisted_path("plan.steps"))
        self.assertFalse(_attestation_allowlisted_path("capabilities.vermyth"))

    def test_allowlist_prefix_does_not_swallow_adjacent_sibling_field(self) -> None:
        """A future `plan.semantic_recommendation` (singular) key must not match the longer prefix."""
        self.assertTrue(_attestation_allowlisted_path("plan.semantic_recommendations"))
        self.assertFalse(_attestation_allowlisted_path("plan.semantic_recommendation"))
        self.assertFalse(_attestation_allowlisted_path("plan.semantic_recommendation.items"))

    def test_simulated_diff_skips_allowlisted_paths_only(self) -> None:
        def simulated_diff(path: str, reviewed_v: object, current_v: object) -> str | None:
            if _attestation_allowlisted_path(path):
                return None
            if reviewed_v == current_v:
                return None
            return path

        self.assertIsNone(simulated_diff("plan.semantic_recommendations", {"x": 1}, {"x": 2}))
        self.assertEqual(
            simulated_diff("fingerprints.required.spell", "aaa", "bbb"),
            "fingerprints.required.spell",
        )


class TestCompareReviewedBundleFingerprint(unittest.TestCase):
    def _base_bundle(self, *, spell_fp: str) -> dict:
        return {
            "bundle_version": "0.9",
            "environment": {
                "axiomurgy_version": "x",
                "mcp_protocol_version": "y",
                "witness_canonical_json": True,
                "python": {"implementation": "cpython", "major_minor": "3.11", "version": "3.11.0"},
                "platform": {"platform": "linux"},
            },
            "fingerprints": {"required": {"spell": spell_fp}},
            "capabilities": {"envelope": {"kinds": ["read"]}},
        }

    def test_fingerprint_mismatch_is_required(self) -> None:
        reviewed = self._base_bundle(spell_fp="aaa")
        current = self._base_bundle(spell_fp="bbb")
        cmp = compare_reviewed_bundle(reviewed, current)
        self.assertEqual(cmp["status"], "mismatch")
        paths = [d["path"] for d in cmp["diffs"]]
        self.assertIn("fingerprints.required.spell", paths)

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


class TestFetchSemanticRecommendationsContract(unittest.TestCase):
    """Ensures /arcane/recommend receives task-shaped dict input (Vermyth HTTP contract)."""

    @patch.dict(os.environ, {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:9"}, clear=False)
    @patch("axiomurgy.vermyth_integration._client")
    def test_arcane_recommend_receives_dict_input(self, mock_client: MagicMock) -> None:
        from axiomurgy.vermyth_integration import fetch_semantic_recommendations

        mock_instance = MagicMock()
        mock_instance.arcane_recommend.return_value = {"recommendations": [], "skill_id": "axiomurgy.plan"}
        mock_client.return_value = mock_instance
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        resolved = resolve_run_target(spell_path, None, policy_path, ROOT / "artifacts")
        out = fetch_semantic_recommendations(resolved, skill_id="axiomurgy.plan")
        self.assertEqual(out["status"], "ok")
        mock_instance.arcane_recommend.assert_called_once()
        inp = mock_instance.arcane_recommend.call_args.kwargs["input_"]
        self.assertIsInstance(inp, dict)
        self.assertIn("intent", inp)
        self.assertIsInstance(inp["intent"], dict)
        self.assertIn("objective", inp["intent"])


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
