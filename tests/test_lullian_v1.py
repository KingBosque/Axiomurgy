"""Lullian v1: bounded candidate_verification (requires Parthenogenesis + Lullian flags)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import unittest

from axiomurgy.describe import describe_target
from axiomurgy.legacy import main
from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.reasoning_bundle import REASONING_VERSION
from axiomurgy.review import _attestation_allowlisted_path, compare_reviewed_bundle

ROOT = Path(__file__).resolve().parents[1]


def _resolved(p: Path):
    return resolve_run_target(p, None, None, None)


def _gen_lul_env() -> dict:
    return {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
        "AXIOMURGY_REASONING_GENERATION": "1",
        "AXIOMURGY_REASONING_LULLIAN": "1",
    }


class TestLullianGating(unittest.TestCase):
    def tearDown(self) -> None:
        for k in (
            "AXIOMURGY_REASONING",
            "AXIOMURGY_REASONING_EXPERIMENTAL",
            "AXIOMURGY_REASONING_GENERATION",
            "AXIOMURGY_REASONING_LULLIAN",
            "AXIOMURGY_WYRD",
        ):
            os.environ.pop(k, None)

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_lullian_off_omits_block(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_LULLIAN", None)
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        ex = plan["reasoning"]["experimental"]
        self.assertNotIn("candidate_verification", ex)

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_generation_off_omits_lullian_even_if_set(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_GENERATION", None)
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        ex = plan["reasoning"]["experimental"]
        self.assertNotIn("candidate_verification", ex)

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_all_flags_present_deterministic(self) -> None:
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r1 = build_plan_summary(_resolved(spell_path))["reasoning"]["experimental"]["candidate_verification"]
        r2 = build_plan_summary(_resolved(spell_path))["reasoning"]["experimental"]["candidate_verification"]
        self.assertEqual(r1, r2)
        self.assertEqual(r1.get("kind"), "derived")
        self.assertTrue(r1.get("bounded"))
        self.assertEqual(len(r1.get("dimension_order", [])), 8)

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_readonly_empty_candidates(self) -> None:
        spell_path = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        cv = plan["reasoning"]["experimental"]["candidate_verification"]
        self.assertEqual(cv["candidate_results"], [])
        self.assertIn("no_candidates_to_verify", cv.get("notes", []))

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_openapi_risk_reduction_dimensions(self) -> None:
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        results = plan["reasoning"]["experimental"]["candidate_verification"]["candidate_results"]
        self.assertTrue(results)
        risk = next((r for r in results if r.get("candidate_kind") == "risk_reduction_variant"), None)
        self.assertIsNotNone(risk)
        by_dim = {d["dimension"]: d["status"] for d in risk["dimension_results"]}
        self.assertEqual(by_dim.get("friction_reduction"), "improves")
        self.assertEqual(by_dim.get("reversibility"), "improves")

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_wyrd_off_unknown_wyrd_consistency(self) -> None:
        os.environ["AXIOMURGY_WYRD"] = "0"
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        base = plan["reasoning"]["experimental"]["candidate_verification"]["base_plan"]["dimension_results"]
        wy_base = next((d for d in base if d["dimension"] == "wyrd_consistency"), None)
        self.assertIsNotNone(wy_base)
        self.assertEqual(wy_base["status"], "unknown")

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_describe_minimal_verification_shell(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        out = describe_target(_resolved(spell_path))
        cv = out["reasoning"]["experimental"]["candidate_verification"]
        self.assertEqual(cv["candidate_results"], [])
        self.assertIn("plan_path_preferred_for_verification", cv.get("notes", []))

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_attestation_allowlist_kind_path(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.experimental.candidate_verification.kind"))

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    def test_compare_reviewed_ignores_experimental_drift(self) -> None:
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
            "plan": {"reasoning": {"experimental": {"candidate_verification": {"kind": "derived"}}}},
            "describe": {},
        }
        cur = json.loads(json.dumps(reviewed))
        cur["plan"]["reasoning"]["experimental"]["candidate_verification"] = {"kind": "derived", "x": 1}
        self.assertEqual(compare_reviewed_bundle(reviewed, cur)["status"], "exact")

    @mock.patch.dict(os.environ, _gen_lul_env(), clear=False)
    @mock.patch("axiomurgy.vermyth_integration.run_vermyth_gate")
    def test_plan_does_not_call_vermyth(self, gate) -> None:
        spell = str(ROOT / "examples" / "primer_to_axioms.spell.json")
        if not Path(spell).is_file():
            self.skipTest("example missing")
        code = main([spell, "--plan"])
        self.assertEqual(code, 0)
        gate.assert_not_called()


class TestReasoningVersionLullian(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(REASONING_VERSION, "1.7.0")
