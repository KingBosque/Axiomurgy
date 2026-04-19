"""Optional plan/describe reasoning blocks and attestation allowlist."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.describe import describe_target
from axiomurgy.review import _attestation_allowlisted_path, compare_reviewed_bundle
from axiomurgy.reasoning_bundle import REASONING_VERSION, reasoning_enabled
from axiomurgy.wyrd.store import append_node, read_wyrd_hints

ROOT = Path(__file__).resolve().parents[1]


class TestReasoningAllowlist(unittest.TestCase):
    def test_plan_describe_reasoning_paths(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning"))
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.telos.final_cause"))
        self.assertTrue(_attestation_allowlisted_path("describe.reasoning"))
        self.assertTrue(_attestation_allowlisted_path("describe.reasoning.governor.id"))

    def test_adjacent_paths_not_allowlisted(self) -> None:
        self.assertFalse(_attestation_allowlisted_path("plan.reasoning_extra"))
        self.assertFalse(_attestation_allowlisted_path("plan.reason"))


class TestReasoningPlanDescribe(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("AXIOMURGY_REASONING")
        self._old_wyrd = os.environ.get("AXIOMURGY_WYRD")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("AXIOMURGY_REASONING", None)
        else:
            os.environ["AXIOMURGY_REASONING"] = self._old
        if self._old_wyrd is None:
            os.environ.pop("AXIOMURGY_WYRD", None)
        else:
            os.environ["AXIOMURGY_WYRD"] = self._old_wyrd

    def test_default_no_reasoning_keys(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING", None)
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        resolved = resolve_run_target(spell_path, None, None, None)
        plan = build_plan_summary(resolved)
        desc = describe_target(resolved)
        self.assertNotIn("reasoning", plan)
        self.assertNotIn("reasoning", desc)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_reasoning_present_when_enabled(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        resolved = resolve_run_target(spell_path, None, None, None)
        plan = build_plan_summary(resolved)
        desc = describe_target(resolved)
        self.assertIn("reasoning", plan)
        self.assertIn("reasoning", desc)
        r = plan["reasoning"]
        self.assertEqual(r.get("axiomurgy_reasoning_version"), REASONING_VERSION)
        self.assertIn("governor", r)
        self.assertIn("telos", r)
        self.assertIn("final_cause", r["telos"])
        self.assertIn("objectives", r["telos"])
        self.assertIn("dialectic", r)
        self.assertIn("scene", r)
        self.assertIn("habitus", r)
        self.assertIn("correspondence", r)
        self.assertIn("friction", r)
        self.assertIn("combinatorics_search", r)
        self.assertIn("wyrd_hints", r)
        self.assertIn("generation_candidates", r)

    def test_compare_reviewed_ignores_reasoning_drift(self) -> None:
        reviewed = {
            "bundle_version": "0.9",
            "environment": {
                "axiomurgy_version": "x",
                "mcp_protocol_version": "y",
                "witness_canonical_json": True,
                "python": {"implementation": "cpython", "major_minor": "3.11", "version": "3.11.0"},
                "platform": {"platform": "linux"},
            },
            "fingerprints": {"required": {"spell": "a"}, "spellbook": {}, "input_manifest": {"classification": {"summary": {}}}},
            "capabilities": {"envelope": {"kinds": ["policy.evaluate"]}},
            "plan": {"reasoning": {"telos": {"final_cause": "old"}}},
            "describe": {"reasoning": {"x": 1}},
        }
        current = dict(reviewed)
        current["plan"] = dict(reviewed["plan"])
        current["plan"]["reasoning"] = {"telos": {"final_cause": "new"}}
        current["describe"] = dict(reviewed["describe"])
        current["describe"]["reasoning"] = {"x": 2}
        cmp = compare_reviewed_bundle(reviewed, current)
        self.assertEqual(cmp["status"], "exact")


class TestWyrdStore(unittest.TestCase):
    def test_hints_roundtrip(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ad = Path(tmp)
            append_node(ad, "test", {"k": 1})
            hints = read_wyrd_hints(ad)
            self.assertEqual(len(hints), 1)
            self.assertEqual(hints[0]["kind"], "test")
            self.assertEqual(hints[0]["payload"], {"k": 1})

    def test_missing_db_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_wyrd_hints(Path(tmp)), [])


class TestReasoningEnabled(unittest.TestCase):
    def test_flag(self) -> None:
        with mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}):
            self.assertTrue(reasoning_enabled())
        with mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "0"}):
            self.assertFalse(reasoning_enabled())
