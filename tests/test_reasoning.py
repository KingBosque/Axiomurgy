"""Optional plan/describe reasoning blocks and attestation allowlist."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.describe import describe_target
from axiomurgy.review import _attestation_allowlisted_path, compare_reviewed_bundle
from axiomurgy.reasoning_bundle import (
    DERIVED_KEYS_MINIMAL,
    REASONING_EXPERIMENTAL_BLOCK_KEYS,
    REASONING_VERSION,
    reasoning_enabled,
    reasoning_experimental_enabled,
)
from axiomurgy.wyrd.store import append_node, build_wyrd_hints

ROOT = Path(__file__).resolve().parents[1]


class TestReasoningAllowlist(unittest.TestCase):
    def test_minimal_surface_paths_allowlisted(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning"))
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.classification.surface"))
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.telos.final_cause"))
        self.assertTrue(_attestation_allowlisted_path("describe.reasoning.habitus.kind"))

    def test_experimental_subtree_allowlisted(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.experimental"))
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.experimental.friction.overall_friction.value"))

    def test_stray_reasoning_keys_not_allowlisted(self) -> None:
        self.assertFalse(_attestation_allowlisted_path("plan.reasoning_extra"))
        self.assertFalse(_attestation_allowlisted_path("plan.reason"))
        # Top-level phase-advanced keys are not in the contract (use reasoning.experimental.*).
        self.assertFalse(_attestation_allowlisted_path("plan.reasoning.correspondence"))
        self.assertFalse(_attestation_allowlisted_path("plan.reasoning.wyrd_hints"))


class TestReasoningPlanDescribe(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("AXIOMURGY_REASONING")
        self._old_wyrd = os.environ.get("AXIOMURGY_WYRD")
        self._old_exp = os.environ.get("AXIOMURGY_REASONING_EXPERIMENTAL")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("AXIOMURGY_REASONING", None)
        else:
            os.environ["AXIOMURGY_REASONING"] = self._old
        if self._old_wyrd is None:
            os.environ.pop("AXIOMURGY_WYRD", None)
        else:
            os.environ["AXIOMURGY_WYRD"] = self._old_wyrd
        if self._old_exp is None:
            os.environ.pop("AXIOMURGY_REASONING_EXPERIMENTAL", None)
        else:
            os.environ["AXIOMURGY_REASONING_EXPERIMENTAL"] = self._old_exp

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
    def test_reasoning_minimal_surface_when_enabled(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_EXPERIMENTAL", None)
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
        self.assertIn("classification", r)
        self.assertEqual(r["classification"].get("surface"), "minimal_advisory")
        self.assertFalse(r["classification"].get("experimental_enabled"))
        self.assertEqual(
            set(r["classification"]["derived_keys"]),
            set(DERIVED_KEYS_MINIMAL),
            "classification.derived_keys must match the minimal contract exactly",
        )
        self.assertEqual(r["classification"].get("experimental_keys"), [])
        self.assertIn("governor", r)
        self.assertIn("telos", r)
        self.assertIn("final_cause", r["telos"])
        self.assertIn("objectives", r["telos"])
        self.assertIn("dialectic", r)
        self.assertIn("scene", r)
        self.assertIn("habitus", r)
        self.assertEqual(r["habitus"].get("kind"), "descriptive_context")
        self.assertNotIn("experimental", r)
        self.assertNotIn("correspondence", r)
        self.assertNotIn("friction", r)
        self.assertNotIn("combinatorics_search", r)
        self.assertNotIn("wyrd_hints", r)
        self.assertNotIn("generation_candidates", r)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_experimental_block_when_flag(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        resolved = resolve_run_target(spell_path, None, None, None)
        plan = build_plan_summary(resolved)
        r = plan["reasoning"]
        self.assertTrue(r["classification"].get("experimental_enabled"))
        self.assertIn("experimental", r)
        ex = r["experimental"]
        self.assertIn("correspondence", ex)
        self.assertIn("friction", ex)
        self.assertIn("combinatorics_search", ex)
        self.assertIn("wyrd_hints", ex)
        self.assertIn("generation_candidates", ex)
        dk = set(r["classification"]["derived_keys"])
        self.assertTrue(set(DERIVED_KEYS_MINIMAL).issubset(dk))
        self.assertIn("experimental", dk)
        self.assertGreater(len(dk), len(set(DERIVED_KEYS_MINIMAL)))
        self.assertEqual(
            r["classification"]["experimental_keys"],
            sorted(REASONING_EXPERIMENTAL_BLOCK_KEYS),
        )

    def test_experimental_subtree_is_flat_no_nested_classification(self) -> None:
        """reasoning.experimental stays one level: no nested maturity taxonomies."""
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        resolved = resolve_run_target(spell_path, None, None, None)
        with mock.patch.dict(
            os.environ,
            {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"},
            clear=False,
        ):
            from axiomurgy.reasoning_bundle import build_reasoning_payload

            r = build_reasoning_payload(resolved)
        ex = r["experimental"]

        def assert_no_maturity_keys(obj: object, path: str) -> None:
            banned = {"classification", "derived_keys"}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    self.assertNotIn(
                        k,
                        banned,
                        msg=f"unexpected maturity key at {path}.{k} (keep experimental non-recursive)",
                    )
                    assert_no_maturity_keys(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    assert_no_maturity_keys(item, f"{path}[{i}]")

        assert_no_maturity_keys(ex, "experimental")

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
            append_node(ad, "telos", {"final_cause": "probe", "spell_name": "s1", "k": 1})
            hints = build_wyrd_hints(ad, spell_name="s1")
            self.assertEqual(hints["kind"], "derived")
            self.assertEqual(len(hints["recent_nodes"]), 1)
            self.assertEqual(hints["recent_nodes"][0]["kind"], "telos")

    def test_missing_db_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            h = build_wyrd_hints(Path(tmp), spell_name="x")
            self.assertEqual(h["recent_nodes"], [])
            self.assertIn("no_wyrd_database", h["consistency_notes"])


class TestReasoningEnabled(unittest.TestCase):
    def test_flags(self) -> None:
        with mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}):
            self.assertTrue(reasoning_enabled())
        with mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "0"}):
            self.assertFalse(reasoning_enabled())
        with mock.patch.dict(os.environ, {"AXIOMURGY_REASONING_EXPERIMENTAL": "1"}):
            self.assertTrue(reasoning_experimental_enabled())
