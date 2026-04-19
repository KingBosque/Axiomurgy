"""Shadow telos / governor / dialectic: deterministic, plan-aware reasoning."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from axiomurgy.describe import describe_target
from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.reasoning_bundle import REASONING_VERSION

ROOT = Path(__file__).resolve().parents[1]


def _resolved(path: Path):
    return resolve_run_target(path, None, None, None)


class TestReasoningShadowDeterminism(unittest.TestCase):
    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_plan_twice_identical_reasoning(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        r = _resolved(spell_path)
        a = build_plan_summary(r)["reasoning"]
        b = build_plan_summary(r)["reasoning"]
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_step_scores_bounded(self) -> None:
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        plan = build_plan_summary(_resolved(spell_path))
        for ss in plan["reasoning"]["telos"]["step_scores"]:
            self.assertGreaterEqual(ss["step_component"], 0.0)
            self.assertLessEqual(ss["step_component"], 1.0)


class TestReasoningShadowRepresentativeSpells(unittest.TestCase):
    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_readonly_lower_distance_than_openapi(self) -> None:
        ro = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        api = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not ro.is_file() or not api.is_file():
            self.skipTest("examples missing")
        d_ro = build_plan_summary(_resolved(ro))["reasoning"]["telos"]["distance_to_goal"]["value"]
        d_api = build_plan_summary(_resolved(api))["reasoning"]["telos"]["distance_to_goal"]["value"]
        self.assertLessEqual(d_ro, d_api)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_dialectic_episode_nonempty(self) -> None:
        spell_path = ROOT / "examples" / "primer_to_axioms.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        plan = build_plan_summary(_resolved(spell_path))
        dia = plan["reasoning"]["dialectic"]
        self.assertEqual(dia["kind"], "derived")
        self.assertEqual(len(dia["episodes"]), 1)
        ep = dia["episodes"][0]
        self.assertIn("summary", ep["thesis"])
        self.assertIn("summary", ep["antithesis"])
        self.assertIn("summary", ep["synthesis"])
        self.assertTrue(ep["antithesis"]["summary"])  # conservative read is non-empty
        self.assertTrue(ep["selection_basis"])

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_governor_tradeoffs_when_writes(self) -> None:
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        gov = build_plan_summary(_resolved(spell_path))["reasoning"]["governor"]
        self.assertEqual(gov["kind"], "derived")
        self.assertTrue(gov["drives"])
        self.assertTrue(gov["constraints"])
        self.assertTrue(gov["tradeoffs"])


class TestDeclaredTelos(unittest.TestCase):
    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_constraints_telos_declared_kind(self) -> None:
        path = ROOT / "tests" / "fixtures" / "reasoning_declared_telos.spell.json"
        if not path.is_file():
            self.skipTest("fixture missing")
        t = build_plan_summary(_resolved(path))["reasoning"]["telos"]
        self.assertEqual(t["kind"], "declared")
        self.assertEqual(t["final_cause"], "Declared final cause for test")
        self.assertEqual(t["objectives"][0]["id"], "o1")
        self.assertEqual(t["objectives"][0]["kind"], "declared")


class TestReasoningDescribeParity(unittest.TestCase):
    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_describe_telos_matches_plan_steps_count(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        r = _resolved(spell_path)
        plan_n = len(build_plan_summary(r)["steps"])
        desc_n = len(describe_target(r)["reasoning"]["telos"]["step_scores"])
        self.assertEqual(plan_n, desc_n)


class TestExperimentalUnchanged(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_EXPERIMENTAL", None)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_no_experimental_without_flag(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_EXPERIMENTAL", None)
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example spell missing")
        p = build_plan_summary(_resolved(spell_path))
        self.assertNotIn("experimental", p["reasoning"])


class TestReasoningVersion(unittest.TestCase):
    def test_version_bumped_for_shadow_shapes(self) -> None:
        self.assertEqual(REASONING_VERSION, "1.7.0")


class TestCorrespondenceFrictionExperimental(unittest.TestCase):
    """Correspondence + friction: experimental-only, deterministic, plan-derived."""

    def tearDown(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_EXPERIMENTAL", None)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_readonly_repeated_patterns_empty(self) -> None:
        ro = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        if not ro.is_file():
            self.skipTest("example missing")
        ex = build_plan_summary(_resolved(ro))["reasoning"]["experimental"]["correspondence"]
        self.assertEqual(ex["kind"], "derived")
        self.assertEqual(ex["repeated_patterns"], [])

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_double_pipeline_emits_repeated_pattern(self) -> None:
        path = ROOT / "tests" / "fixtures" / "correspondence_double_pipeline.spell.json"
        if not path.is_file():
            self.skipTest("fixture missing")
        ex = build_plan_summary(_resolved(path))["reasoning"]["experimental"]["correspondence"]
        self.assertTrue(ex["repeated_patterns"])
        self.assertEqual(ex["repeated_patterns"][0]["pattern"], "read_middle_write_pipeline_duplicated")

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_correspondence_clusters_and_objective_links(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        co = build_plan_summary(_resolved(spell_path))["reasoning"]["experimental"]["correspondence"]
        self.assertGreaterEqual(len(co["clusters"]), 3)
        ids = {c["cluster_id"] for c in co["clusters"]}
        self.assertTrue(all("objective_ids" in ol for ol in co["objective_links"]))
        self.assertTrue(all(ol["cluster_id"] in ids for ol in co["objective_links"]))

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_friction_readonly_lower_than_openapi(self) -> None:
        ro = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        api = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not ro.is_file() or not api.is_file():
            self.skipTest("examples missing")
        f_ro = build_plan_summary(_resolved(ro))["reasoning"]["experimental"]["friction"]
        f_api = build_plan_summary(_resolved(api))["reasoning"]["experimental"]["friction"]
        self.assertLess(f_ro["overall_friction"]["value"], f_api["overall_friction"]["value"])
        for ps in f_ro["per_step_friction"]:
            self.assertGreaterEqual(ps["value"], 0.0)
            self.assertLessEqual(ps["value"], 1.0)
            self.assertIn(ps["interpretation"], ("low", "medium", "high"))

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_openapi_write_step_fallback_absence_false_when_compensated(self) -> None:
        api = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not api.is_file():
            self.skipTest("example missing")
        fr = build_plan_summary(_resolved(api))["reasoning"]["experimental"]["friction"]
        by_id = {p["step_id"]: p for p in fr["per_step_friction"]}
        self.assertIn("create_ticket", by_id)
        self.assertFalse(by_id["create_ticket"]["fallback_absence"])

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False)
    def test_inbox_approval_step_human_review_factor(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        fr = build_plan_summary(_resolved(spell_path))["reasoning"]["experimental"]["friction"]
        by_id = {p["step_id"]: p for p in fr["per_step_friction"]}
        self.assertIn("human_review_gate", by_id["approval"]["risk_factors"])
        self.assertTrue(fr["bottlenecks"])

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1"}, clear=False)
    def test_plan_steps_identical_with_or_without_experimental(self) -> None:
        os.environ.pop("AXIOMURGY_REASONING_EXPERIMENTAL", None)
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        base = build_plan_summary(r)
        with mock.patch.dict(os.environ, {"AXIOMURGY_REASONING_EXPERIMENTAL": "1"}, clear=False):
            exp = build_plan_summary(r)
        self.assertEqual(base.get("steps"), exp.get("steps"))
        self.assertEqual(base.get("write_steps"), exp.get("write_steps"))
