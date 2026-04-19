"""Parthenogenesis v1: bounded generation_candidates (plan path, review-bound)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import unittest

from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.reasoning_bundle import REASONING_VERSION
from axiomurgy.generation import DEFAULT_CANDIDATE_CAP, reasoning_generation_enabled
from axiomurgy.review import compare_reviewed_bundle, _attestation_allowlisted_path

ROOT = Path(__file__).resolve().parents[1]


def _resolved(p: Path):
    return resolve_run_target(p, None, None, None)


def _gen_env() -> dict:
    return {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
        "AXIOMURGY_REASONING_GENERATION": "1",
    }


class TestParthenogenesisGating(unittest.TestCase):
    def tearDown(self) -> None:
        for k in (
            "AXIOMURGY_REASONING",
            "AXIOMURGY_REASONING_EXPERIMENTAL",
            "AXIOMURGY_REASONING_GENERATION",
        ):
            os.environ.pop(k, None)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_generation_off_by_default(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_GENERATION", None)
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        gc = plan["reasoning"]["experimental"]["generation_candidates"]
        self.assertFalse(gc.get("generation_enabled"))

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_generation_on_requires_all_flags(self) -> None:
        self.assertTrue(reasoning_generation_enabled())
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        gc = plan["reasoning"]["experimental"]["generation_candidates"]
        self.assertTrue(gc.get("generation_enabled"))
        self.assertLessEqual(len(gc["candidates"]), DEFAULT_CANDIDATE_CAP)


class TestParthenogenesisCandidates(unittest.TestCase):
    def tearDown(self) -> None:
        for k in ("AXIOMURGY_REASONING", "AXIOMURGY_REASONING_EXPERIMENTAL", "AXIOMURGY_REASONING_GENERATION"):
            os.environ.pop(k, None)

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_readonly_single_step_empty(self) -> None:
        spell_path = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        gc = plan["reasoning"]["experimental"]["generation_candidates"]
        self.assertEqual(gc["candidates"], [])

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_openapi_has_risk_or_boundary(self) -> None:
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        kinds = {c["candidate_kind"] for c in plan["reasoning"]["experimental"]["generation_candidates"]["candidates"]}
        self.assertTrue(
            kinds & {"risk_reduction_variant", "boundary_isolation_variant"},
            msg=f"got {kinds}",
        )

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_inbox_approval_or_subgoal(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        kinds = {c["candidate_kind"] for c in plan["reasoning"]["experimental"]["generation_candidates"]["candidates"]}
        self.assertTrue(kinds & {"approval_first_variant", "subgoal_split", "risk_reduction_variant"})

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_every_candidate_review_bound(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        for c in plan["reasoning"]["experimental"]["generation_candidates"]["candidates"]:
            self.assertFalse(c["execution_ready"])
            self.assertTrue(c["review_required"])
            self.assertIn("parent_refs", c)
            self.assertIn("generation_reason", c)
            self.assertIn("target_telos_objective_ids", c)
            self.assertTrue(c["candidate_id"])

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_describe_skips_generation(self) -> None:
        from axiomurgy.describe import describe_target

        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        out = describe_target(r)
        gc = out["reasoning"]["experimental"]["generation_candidates"]
        self.assertEqual(gc["candidates"], [])
        self.assertIn("plan_path_preferred_for_generation", gc.get("notes", []))


class TestParthenogenesisAttestationVermyth(unittest.TestCase):
    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    def test_allowlist_generation_path(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.experimental.generation_candidates.candidates"))

    def test_compare_ignores_reasoning(self) -> None:
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
            "plan": {"reasoning": {"experimental": {"generation_candidates": {"candidates": []}}}},
            "describe": {},
        }
        cur = dict(reviewed)
        cur["plan"] = dict(reviewed["plan"])
        cur["plan"]["reasoning"] = {"experimental": {"generation_candidates": {"candidates": [{"x": 1}]}}}
        self.assertEqual(compare_reviewed_bundle(reviewed, cur)["status"], "exact")

    @mock.patch.dict(os.environ, _gen_env(), clear=False)
    @mock.patch("axiomurgy.vermyth_integration.run_vermyth_gate")
    def test_plan_does_not_call_vermyth(self, gate) -> None:
        from axiomurgy.legacy import main

        spell = str(ROOT / "examples" / "primer_to_axioms.spell.json")
        if not Path(spell).is_file():
            self.skipTest("example missing")
        code = main([spell, "--plan"])
        self.assertEqual(code, 0)
        gate.assert_not_called()


class TestReasoningVersionParthenogenesis(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(REASONING_VERSION, "1.7.0")
