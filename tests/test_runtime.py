from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_runtime():
    spec = importlib.util.spec_from_file_location("axiomurgy_runtime", ROOT / "axiomurgy.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AxiomurgyRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = load_runtime()
        cls.capabilities = ["approve", "read", "reason", "simulate", "transform", "verify", "write"]

    def test_can_load_and_compile_all_examples_and_spellbook(self):
        for path in sorted((ROOT / "examples").glob("*.json")):
            spell = self.runtime.load_spell(path)
            plan = self.runtime.compile_plan(spell)
            self.assertTrue(plan, f"expected a non-empty plan for {path}")
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        self.assertEqual(resolved.spellbook.name, "primer_codex")
        self.assertEqual(resolved.entrypoint, "publish_codex")
        self.assertTrue(self.runtime.compile_plan(resolved.spell))

    def test_describe_and_plan_spellbook_entrypoint_surface_approvals_and_writes(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        description = self.runtime.describe_target(resolved)
        self.assertEqual(description["mode"], "describe")
        self.assertEqual(description["spellbook"]["name"], "primer_codex")
        self.assertEqual(description["spellbook"]["resolved_entrypoint"], "publish_codex")

        plan = self.runtime.build_plan_summary(resolved)
        self.assertEqual(plan["mode"], "plan")
        self.assertTrue(plan["steps"])
        self.assertTrue(plan["write_steps"])
        self.assertTrue(plan["required_approvals"])
        self.assertEqual(plan["manifest"]["policy_path"], str(resolved.policy_path))
        self.assertEqual(plan["manifest"]["artifact_dir"], str(resolved.artifact_dir))
        self.assertTrue(any(item["step_id"] == "publish" for item in plan["required_approvals"]))
        self.assertTrue(any(item["step_id"] == "publish" for item in plan["write_steps"]))

        granted_plan = self.runtime.build_plan_summary(resolved, approvals={"publish"})
        publish_approval = next(item for item in granted_plan["required_approvals"] if item["step_id"] == "publish")
        self.assertTrue(publish_approval["granted"])

    def test_lint_spellbook_succeeds(self):
        lint = self.runtime.lint_target(ROOT / "spellbooks" / "primer_codex")
        self.assertTrue(lint["ok"], lint)
        self.assertFalse(lint["errors"], lint)
        self.assertIn("publish_codex", lint["entrypoints"])

    def test_lint_catches_unknown_rune_and_broken_dependency(self):
        bad_spell = {
            "spell": "bad_spell",
            "intent": "Exercise deterministic lint failures.",
            "graph": [
                {
                    "id": "bad_step",
                    "rune": "unknown.rune",
                    "effect": "transform",
                    "args": {"from": "$missing"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.spell.json"
            path.write_text(json.dumps(bad_spell, indent=2), encoding="utf-8")
            lint = self.runtime.lint_target(path)
        self.assertFalse(lint["ok"], lint)
        codes = {item["code"] for item in lint["errors"]}
        self.assertIn("unknown_rune", codes)
        self.assertIn("graph", codes)

    def test_review_bundle_for_spellbook_contains_preflight_and_fingerprints(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        bundle = self.runtime.build_review_bundle(resolved)
        self.assertEqual(bundle["bundle_version"], "0.9")
        self.assertIn("environment", bundle)
        self.assertIn("describe", bundle)
        self.assertIn("lint", bundle)
        self.assertIn("plan", bundle)
        self.assertIn("approval_manifest", bundle)
        self.assertIn("fingerprints", bundle)
        self.assertIn("required", bundle["fingerprints"])
        self.assertIn("capabilities", bundle)
        self.assertIn("required", bundle["capabilities"])
        self.assertIn("envelope", bundle["capabilities"])
        self.assertIn("kinds", bundle["capabilities"]["envelope"])
        self.assertIsInstance(bundle["capabilities"]["envelope"]["kinds"], list)
        self.assertTrue(bundle["capabilities"]["envelope"]["kinds"])

    def test_verify_review_bundle_detects_spell_change(self):
        base = ROOT / "examples" / "primer_to_axioms.spell.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_spell = Path(tmpdir) / "spell.spell.json"
            tmp_spell.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
            resolved = self.runtime.resolve_run_target(tmp_spell, None, None, None)
            reviewed = self.runtime.build_review_bundle(resolved)
            # Mutate content (behavior-affecting) and ensure mismatch is detected.
            tmp_spell.write_text(tmp_spell.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            resolved2 = self.runtime.resolve_run_target(tmp_spell, None, None, None)
            current = self.runtime.build_review_bundle(resolved2)
            cmp = self.runtime.compare_reviewed_bundle(reviewed, current)
            self.assertEqual(cmp["status"], "mismatch", cmp)

    def test_execute_attestation_exact_against_review_bundle(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        reviewed = self.runtime.build_review_bundle(resolved)
        attestation = self.runtime.compute_attestation(reviewed, resolved, approvals={"publish"})
        self.assertIn(attestation["status"], ("exact", "partial"))

    def test_compute_attestation_accepts_v08_bundle_missing_capabilities(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        reviewed = self.runtime.build_review_bundle(resolved)
        reviewed.pop("capabilities", None)  # v0.8 bundle shape
        attestation = self.runtime.compute_attestation(reviewed, resolved, approvals={"publish"})
        self.assertIn(attestation["status"], ("exact", "partial", "mismatch"))

    def test_attestation_mismatch_on_undeclared_capability_use(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        reviewed = self.runtime.build_review_bundle(resolved)
        # Deliberately restrict reviewed envelope to trigger overreach while keeping fingerprints stable.
        reviewed["capabilities"]["envelope"]["kinds"] = [k for k in reviewed["capabilities"]["envelope"]["kinds"] if k != "filesystem.write"]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            result = self.runtime.execute_spell(
                resolved.spell,
                self.capabilities,
                {"publish"},
                False,
                resolved.policy_path,
                out_dir,
                reviewed_bundle=reviewed,
            )
            self.assertIn("capabilities", result)
            self.assertIn("filesystem.write", result["capabilities"]["overreach"])

    def test_enforce_blocks_undeclared_capability_use(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        reviewed = self.runtime.build_review_bundle(resolved)
        reviewed["capabilities"]["envelope"]["kinds"] = [k for k in reviewed["capabilities"]["envelope"]["kinds"] if k != "filesystem.write"]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            result = self.runtime.execute_spell(
                resolved.spell,
                self.capabilities,
                {"publish"},
                False,
                resolved.policy_path,
                out_dir,
                reviewed_bundle=reviewed,
                enforce_review_bundle=True,
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result.get("execution_outcome"), None)  # set at CLI layer
            self.assertTrue((result.get("blocked") or {}).get("source") in ("review_envelope", None))
            raw_trace = json.loads((out_dir / f"{resolved.spell.name}.trace.raw.json").read_text(encoding="utf-8"))
            diff_trace = json.loads((out_dir / f"{resolved.spell.name}.trace.json").read_text(encoding="utf-8"))
            self.assertTrue(raw_trace.get("capability_denials"))
            self.assertTrue(diff_trace.get("capability_denials"))
            self.assertNotRegex(json.dumps(diff_trace.get("capability_denials")), r"[A-Za-z]:\\\\")

    def test_backward_compatible_behavior_without_enforcement_flag(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        reviewed = self.runtime.build_review_bundle(resolved)
        reviewed.pop("capabilities", None)  # simulate v0.8 bundle
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            result = self.runtime.execute_spell(
                resolved.spell,
                self.capabilities,
                {"publish"},
                False,
                resolved.policy_path,
                out_dir,
                reviewed_bundle=reviewed,
                enforce_review_bundle=True,
            )
            # compat mode: no envelope => no enforcement
            self.assertIn(result["status"], ("succeeded", "failed"))

    def test_ouroboros_chamber_accepts_and_rejects_deterministically(self):
        resolved = self.runtime.resolve_run_target(ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None)
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg = {
                "max_revolutions": 3,
                "flux_budget": 3,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0, 3.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            self.assertEqual(result["mode"], "cycle")
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            self.assertTrue(any(r.get("accepted") for r in witness.get("revolutions", [])))
            self.assertTrue(any(r.get("rejected") for r in witness.get("revolutions", [])))

    def test_ouroboros_mutation_allowlist_blocks(self):
        resolved = self.runtime.resolve_run_target(ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None)
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg = {
                "max_revolutions": 1,
                "flux_budget": 1,
                "plateau_window": 1,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.not_score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 1},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            with self.assertRaises(Exception):
                self.runtime.ouroboros_chamber(
                    resolved,
                    cycle_config_path=cfg_path,
                    approvals=set(),
                    simulate=False,
                    reviewed_bundle=None,
                    enforce_review_bundle=False,
                )

    def test_cycle_config_rejects_both_mutation_families_and_targets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 1,
                        "target_metric": {"kind": "fixture_score", "path": "x.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0]}],
                        "mutation_families": [
                            {"family": "enum", "path": "spell.inputs.score", "candidates": [1.0]}
                        ],
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 1},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(self.runtime.SpellValidationError):
                self.runtime.load_cycle_config(cfg_path)

    def test_expand_cycle_proposals_order_and_proposal_ids_stable(self):
        cfg = self.runtime.load_cycle_config(ROOT / "examples" / "cycles" / "ouroboros_cycle_v12.json")
        p1 = self.runtime.expand_cycle_proposals(cfg)
        p2 = self.runtime.expand_cycle_proposals(cfg)
        self.assertEqual(len(p1), 6)
        self.assertEqual(p1, p2)
        self.assertEqual([p["ordering_index"] for p in p1], list(range(6)))
        ids = [p["proposal_id"] for p in p1]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ouroboros_recall_bounded_and_top_level_recall(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture_v12.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=ROOT / "examples" / "cycles" / "ouroboros_cycle_v12.json",
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            self.assertIn("recall", witness)
            rec = witness["recall"]
            self.assertIn("best_score_so_far", rec)
            self.assertLessEqual(len(rec["recent_k_successes"]), 2)
            self.assertLessEqual(len(rec["recent_k_failures"]), 2)
            for rev in witness.get("revolutions", []):
                self.assertIn("recall_snapshot", rev)
                self.assertIn("proposal_id", rev)
                self.assertIn("score_before", rev)
                self.assertIn("score_after", rev)
                self.assertIn("accept_reject_reason", rev)

    def test_ouroboros_diffable_witness_no_windows_paths(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture_v12.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=ROOT / "examples" / "cycles" / "ouroboros_cycle_v12.json",
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            diff_text = Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8")
            self.assertNotRegex(diff_text, r"[A-Za-z]:\\\\")

    def test_ouroboros_enforce_review_bundle_blocks_overreach(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        reviewed = self.runtime.build_review_bundle(resolved)
        reviewed["capabilities"]["envelope"]["kinds"] = [
            k for k in reviewed["capabilities"]["envelope"]["kinds"] if k != "filesystem.write"
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg = {
                "max_revolutions": 1,
                "flux_budget": 1,
                "plateau_window": 1,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 1},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=reviewed,
                enforce_review_bundle=True,
            )
            self.assertEqual(result["mode"], "cycle")
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            # v1.3: envelope overreach is skipped in preflight (no veil attempt).
            self.assertEqual(witness.get("flux_attempts"), 0)
            self.assertTrue(witness.get("preflight_skips"))
            self.assertIn("filesystem.write", json.dumps(witness["preflight_skips"]))
            self.assertEqual(witness.get("revolutions"), [])

    def test_ouroboros_skips_linear_rejected_without_retry(self):
        """Rejected proposal_id is never attempted again in the same run (linear list)."""
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg = {
                "max_revolutions": 10,
                "flux_budget": 10,
                "plateau_window": 10,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [0.0, 2.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 10, "min_improvement": 0.0, "no_improve_for": 10},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            props = self.runtime.expand_cycle_proposals(self.runtime.load_cycle_config(cfg_path))
            pid_bad = props[0]["proposal_id"]
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            n_bad = sum(1 for r in witness["revolutions"] if r.get("proposal_id") == pid_bad)
            self.assertEqual(n_bad, 1)

    def test_proposal_id_unifies_int_and_float_candidates(self):
        a = self.runtime.proposal_id("enum", "spell.inputs.score", 1)
        b = self.runtime.proposal_id("enum", "spell.inputs.score", 1.0)
        self.assertEqual(a, b)

    def test_expand_cycle_proposals_dedupes_identical_proposal_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 4,
                        "flux_budget": 4,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "x.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [
                            {"path": "spell.inputs.score", "choices": [2.0, 2.0, 1, 1.0]}
                        ],
                        "stop_conditions": {"max_failures": 4, "min_improvement": 0.0, "no_improve_for": 4},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
        self.assertEqual(len(props), 2)
        self.assertEqual(len({p["proposal_id"] for p in props}), 2)

    def test_ouroboros_chamber_removes_stale_shadow_spells(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            chamber = resolved.artifact_dir / "ouroboros"
            chamber.mkdir(parents=True, exist_ok=True)
            stale = chamber / "rev_099.spell.json"
            stale.write_text("{}", encoding="utf-8")
            cfg = {
                "max_revolutions": 1,
                "flux_budget": 1,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                "run_capsule": {"enabled": False},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            self.assertFalse(stale.exists())
            self.assertTrue((chamber / "rev_001.spell.json").exists())

    def test_ouroboros_diffable_witness_config_path_is_portable(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            resolved.artifact_dir = Path(tmpdir)
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path.resolve(),
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            diff = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            cfg_out = diff.get("config_path", "")
            self.assertTrue(cfg_out.startswith("repo:") or cfg_out == "<opaque_path>")

    def test_v13_plan_ouroboros_proposals_record_shape_and_ranking(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 3,
                        "flux_budget": 3,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
            metric_abs = str((ad / "ouroboros_score.json").resolve())
            plan = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
        self.assertEqual(plan["proposal_plan_version"], "1.5.0")
        self.assertEqual(len(plan["ranked_proposals"]), len(props))
        ranks = [self.runtime._admissibility_status_rank(r["admissibility_status"]) for r in plan["ranked_proposals"]]
        self.assertEqual(ranks, sorted(ranks))
        for r in plan["ranked_proposals"]:
            self.assertIn(r["admissibility_status"], ("admissible", "uncertain", "inadmissible"))
            self.assertEqual(r["predicted_capabilities"], sorted(r["predicted_capabilities"]))
            self.assertIn("effect_signature", r)
            self.assertIn("effect_signature_id", r)
            self.assertIn("signature_rank", r)
            self.assertIn("duplicate_of_signature", r)
        self.assertEqual(
            plan["diversification_summary"]["diversification_mode"],
            "round_robin_by_effect_signature_within_admissibility_tier",
        )
        self.assertEqual(plan["score_channel_contract"]["score_channel_status"], "aligned")
        self.assertIn("score_channel_summary", plan)

    def test_v13_proposal_plan_deterministic_without_review_bundle(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 2,
                        "flux_budget": 2,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0, 2.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
            metric_abs = str((ad / "ouroboros_score.json").resolve())
            p1 = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            p2 = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
        self.assertEqual(self.runtime.canonical_json(p1), self.runtime.canonical_json(p2))

    def test_v13_preflight_skip_matches_plan_inadmissible_ids(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        reviewed = self.runtime.build_review_bundle(resolved)
        reviewed["capabilities"]["envelope"]["kinds"] = [
            k for k in reviewed["capabilities"]["envelope"]["kinds"] if k != "filesystem.write"
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg = {
                "max_revolutions": 2,
                "flux_budget": 2,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 3.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=reviewed,
                enforce_review_bundle=False,
            )
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            raw_plan = json.loads(Path(result["proposal_plan_raw_path"]).read_text(encoding="utf-8"))
        skipped = raw_plan["skipped_inadmissible_proposal_ids"]
        skip_witness = [x["proposal_id"] for x in witness["preflight_skips"]]
        self.assertEqual(sorted(skipped), sorted(skip_witness))
        self.assertEqual(witness["flux_attempts"], 0)

    def test_v13_proposal_plan_diff_no_windows_paths(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path.resolve(),
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            diff_text = Path(result["proposal_plan_path"]).read_text(encoding="utf-8")
            self.assertNotRegex(diff_text, r"[A-Za-z]:\\\\")

    def test_v14_diversified_ranking_interleaves_distinct_effect_signatures(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture_v12.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 4,
                        "flux_budget": 4,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score", "spell.inputs.note"],
                        "mutation_targets": [
                            {"path": "spell.inputs.score", "choices": [1.0, 2.0]},
                            {"path": "spell.inputs.note", "choices": ["x", "y"]},
                        ],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 4, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
            self.assertEqual(len(props), 4)
            metric_abs = str((ad / "ouroboros_score.json").resolve())
            plan = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
        ranked = plan["ranked_proposals"]
        adm = [r for r in ranked if r["admissibility_status"] == "admissible"]
        sig_ids = {str(r["effect_signature_id"]) for r in adm}
        self.assertEqual(len(sig_ids), 2)
        self.assertEqual(
            plan["diversification_summary"]["per_tier"]["admissible"]["distinct_effect_signatures"],
            2,
        )
        # ordering_index order was score, score, note, note — diversification interleaves by signature.
        by_oid = {int(r["ordering_index"]): r["proposal_id"] for r in adm}
        expect_order = [by_oid[0], by_oid[2], by_oid[1], by_oid[3]]
        self.assertEqual([r["proposal_id"] for r in adm], expect_order)

    def test_v15_score_channel_clear_break_redirects_metric_write(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 2,
                        "flux_budget": 2,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score", "spell.inputs.score_path"],
                        "mutation_targets": [
                            {"path": "spell.inputs.score", "choices": [1.0]},
                            {"path": "spell.inputs.score_path", "choices": ["artifacts/other_score.json"]},
                        ],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
            metric_abs = str((ad / "ouroboros_score.json").resolve())
            plan = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
        self.assertEqual(plan["score_channel_contract"]["score_channel_status"], "aligned")
        by_pid = {r["proposal_id"]: r for r in plan["proposals"]}
        bad = [r for r in plan["proposals"] if r["mutation_target"] == "spell.inputs.score_path"]
        self.assertTrue(bad)
        self.assertEqual(bad[0]["admissibility_status"], "inadmissible")
        self.assertTrue(bad[0].get("score_channel_clear_break"))
        good = [r for r in plan["proposals"] if r["mutation_target"] == "spell.inputs.score"]
        self.assertEqual(good[0]["admissibility_status"], "admissible")
        self.assertEqual(good[0].get("score_channel_preserved"), True)

    def test_v15_score_channel_uncertain_without_clear_baseline(self):
        """When baseline is not aligned, redirect proposals are uncertain — not score-channel inadmissible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            sp = ad / "no_write_metric.spell.json"
            sp.write_text(
                json.dumps(
                    {
                        "spell": "no_write_metric",
                        "intent": "test",
                        "inputs": {"score_path": "artifacts/x.json"},
                        "constraints": {"risk": "low"},
                        "graph": [
                            {
                                "id": "noop",
                                "rune": "mirror.read",
                                "effect": "read",
                                "args": {"input": ["file://README.md"]},
                            }
                        ],
                        "rollback": [],
                        "witness": {"record": False},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            resolved = self.runtime.resolve_run_target(sp, None, None, None)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score_path"],
                        "mutation_targets": [
                            {"path": "spell.inputs.score_path", "choices": ["artifacts/y.json"]},
                        ],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
            metric_abs = str((ad / "ouroboros_score.json").resolve())
            plan = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
        self.assertEqual(plan["score_channel_contract"]["score_channel_status"], "uncertain")
        r0 = plan["proposals"][0]
        self.assertNotEqual(r0.get("admissibility_status"), "inadmissible")
        self.assertFalse(r0.get("score_channel_clear_break"))

    def test_v15_cycle_config_optional_score_channel_keys_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
        self.assertEqual(cfg["score_channel_sensitive_paths"], [])
        self.assertFalse(cfg["block_score_channel_sensitive_mutations"])

    def test_v15_sensitive_mutation_blocked_when_opt_in(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                        "score_channel_sensitive_paths": ["spell.inputs.score"],
                        "block_score_channel_sensitive_mutations": True,
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
            props = self.runtime.expand_cycle_proposals(cfg)
            metric_abs = str((ad / "ouroboros_score.json").resolve())
            plan = self.runtime.plan_ouroboros_proposals(
                resolved,
                proposals=props,
                allowlist=cfg["mutation_target_allowlist"],
                metric_abs=metric_abs,
                metric_rel="ouroboros_score.json",
                reviewed_bundle=None,
                enforce_review_bundle=False,
                score_channel_sensitive_paths=cfg["score_channel_sensitive_paths"],
                block_score_channel_sensitive_mutations=True,
            )
        r0 = plan["proposals"][0]
        self.assertEqual(r0["admissibility_status"], "inadmissible")
        self.assertIn("score_channel_sensitive_mutation_blocked", r0["reasons"])

    def test_v16_default_acceptance_contract_backward_compat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.42, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(cfg_path)
        ac = cfg["acceptance_contract"]
        self.assertEqual(ac["required_improvement"], 0.42)
        self.assertEqual(ac["primary_metric"], "maximize")
        self.assertEqual(ac["tie_breakers"], ["lower_ordering_index"])

    def test_v16_ouroboros_witness_has_seal_and_acceptance_summary(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 2,
                        "flux_budget": 2,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            witness_path = Path(result["ouroboros_witness_path"])
            diff_text = witness_path.read_text(encoding="utf-8")
            w = json.loads(diff_text)
        self.assertIn("acceptance_contract", w)
        self.assertIn("acceptance_summary", w)
        self.assertIn("accepted_by_contract", w["acceptance_summary"])
        for rev in w.get("revolutions", []):
            if rev.get("execution_result", {}).get("status") not in ("skipped", None):
                self.assertIsNotNone(rev.get("seal_decision"))
        self.assertNotRegex(diff_text, r"[A-Za-z]:\\\\")

    def test_v16_evaluate_acceptance_contract_tie_break(self):
        contract = {
            "primary_metric": "maximize",
            "required_improvement": 0.0,
            "guardrails": [],
            "tie_breakers": ["lower_ordering_index"],
            "reject_if": {
                "score_channel_worsens": False,
                "admissibility_worsens": False,
                "capability_envelope_worsens": False,
            },
        }
        rec = {"admissibility_status": "admissible", "score_channel_status": "aligned", "capability_envelope_compatibility": "not_applicable"}
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            initial = {"ouroboros_score.json": 1.0}
            seal = self.runtime.evaluate_acceptance_contract(
                artifact_dir=ad,
                contract=contract,
                execution_succeeded=True,
                candidate_primary=1.0,
                best_primary=1.0,
                initial_metrics=initial,
                metrics_at_best=dict(initial),
                metrics_at_last_accept=dict(initial),
                rec=rec,
                last_accepted_rec=None,
                best_ordering_index=5,
                candidate_ordering_index=2,
                revolution=2,
                last_accepted_revolution=None,
            )
        self.assertEqual(seal["decision"], "accept")
        self.assertEqual(seal["reasons"][0], "contract_accept:tie_break")

    def test_v16_guardrail_rejects_when_primary_would_pass(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [99.0]}],
                        "acceptance_contract": {
                            "guardrails": [
                                {
                                    "metric_path": "ouroboros_score.json",
                                    "comparator": "<=",
                                    "baseline_source": "initial_baseline",
                                }
                            ]
                        },
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            w = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
        self.assertGreater(w["acceptance_summary"].get("rejected_by_guardrail", 0), 0)
        reasons = [r.get("accept_reject_reason") for r in w.get("revolutions", [])]
        self.assertIn("contract_reject:guardrail", reasons)

    def test_v16_reject_if_score_channel_worsens(self):
        contract = {
            "primary_metric": "maximize",
            "required_improvement": 0.0,
            "guardrails": [],
            "tie_breakers": ["lower_ordering_index"],
            "reject_if": {
                "score_channel_worsens": True,
                "admissibility_worsens": False,
                "capability_envelope_worsens": False,
            },
        }
        last_rec = {"score_channel_status": "aligned", "admissibility_status": "admissible", "capability_envelope_compatibility": "not_applicable"}
        rec = {"score_channel_status": "uncertain", "admissibility_status": "admissible", "capability_envelope_compatibility": "not_applicable"}
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            initial = {"ouroboros_score.json": 5.0}
            seal = self.runtime.evaluate_acceptance_contract(
                artifact_dir=ad,
                contract=contract,
                execution_succeeded=True,
                candidate_primary=10.0,
                best_primary=1.0,
                initial_metrics=initial,
                metrics_at_best=dict(initial),
                metrics_at_last_accept=dict(initial),
                rec=rec,
                last_accepted_rec=last_rec,
                best_ordering_index=0,
                candidate_ordering_index=1,
                revolution=2,
                last_accepted_revolution=1,
            )
        self.assertEqual(seal["decision"], "reject")
        self.assertIn("reject_if:score_channel_worsens", seal["reasons"])

    def test_v17_baseline_registry_and_promotion_on_accept(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 2,
                        "flux_budget": 2,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [3.0, 0.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            w = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
        reg = w["baseline_registry"]
        self.assertGreaterEqual(len(reg), 2)
        self.assertEqual(reg[0]["baseline_id"], "bl_0001")
        self.assertEqual(reg[0]["status"], "superseded")
        self.assertEqual(reg[0]["parent_baseline_id"], None)
        promoted = [r for r in reg if r.get("status") == "active"]
        self.assertEqual(len(promoted), 1)
        self.assertEqual(w["lineage_summary"]["final_active_baseline_id"], promoted[0]["baseline_id"])
        self.assertEqual(w["lineage_summary"]["total_promotions"], len(w["promotion_records"]))
        self.assertGreaterEqual(w["lineage_summary"]["total_promotions"], 1)
        pr0 = w["promotion_records"][0]
        self.assertEqual(pr0["from_baseline_id"], "bl_0001")
        self.assertEqual(pr0["to_baseline_id"], promoted[0]["baseline_id"])
        self.assertIn("metrics_before", pr0)
        self.assertIn("metrics_after", pr0)
        for rev in w["revolutions"]:
            self.assertIn("active_baseline_id", rev)
        exec_revs = [r for r in w["revolutions"] if r.get("seal_decision")]
        for rev in exec_revs:
            self.assertIn("baseline_reference_used_id", rev["seal_decision"])
            prim = rev["seal_decision"]["baseline_reference_used_id"]["primary"]
            self.assertTrue(prim.startswith("bl_"))
        self.assertEqual(exec_revs[0]["seal_decision"]["baseline_reference_used_id"]["primary"], "bl_0001")
        if len(exec_revs) > 1:
            self.assertEqual(exec_revs[1]["seal_decision"]["baseline_reference_used_id"]["primary"], "bl_0002")

    def test_v17_no_promotion_on_reject_preserves_active(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [0.5]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            w = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
        self.assertEqual(w["lineage_summary"]["total_promotions"], 0)
        self.assertEqual(w["lineage_summary"]["final_active_baseline_id"], "bl_0001")
        self.assertEqual(len(w["promotion_records"]), 0)

    def test_v17_lineage_deterministic_ids_two_runs(self):
        def run_once():
            resolved = self.runtime.resolve_run_target(
                ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
            )
            with tempfile.TemporaryDirectory() as tmpdir:
                resolved.artifact_dir = Path(tmpdir)
                cfg_path = Path(tmpdir) / "cycle.json"
                cfg_path.write_text(
                    json.dumps(
                        {
                            "max_revolutions": 1,
                            "flux_budget": 1,
                            "plateau_window": 2,
                            "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                            "mutation_target_allowlist": ["spell.inputs.score"],
                            "mutation_targets": [{"path": "spell.inputs.score", "choices": [5.0]}],
                            "rollback_mode": "shadow_copy",
                            "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                result = self.runtime.ouroboros_chamber(
                    resolved,
                    cycle_config_path=cfg_path,
                    approvals=set(),
                    simulate=False,
                    reviewed_bundle=None,
                    enforce_review_bundle=False,
                )
                return json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))

        w1 = run_once()
        w2 = run_once()
        self.assertEqual([b["baseline_id"] for b in w1["baseline_registry"]], [b["baseline_id"] for b in w2["baseline_registry"]])
        self.assertEqual(w1["lineage_summary"]["final_active_baseline_id"], w2["lineage_summary"]["final_active_baseline_id"])

    def test_v17_diffable_lineage_fields_portable(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            diff_text = Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8")
        self.assertNotRegex(diff_text, r"[A-Za-z]:\\\\")
        self.assertIn("bl_0001", diff_text)

    def test_v17_lineage_policy_optional(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "c.json"
            p.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 1,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 1},
                        "lineage_policy": {"record_rejected_snapshots": False},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            cfg = self.runtime.load_cycle_config(p)
        self.assertEqual(cfg["lineage_policy"]["record_rejected_snapshots"], False)

    def test_v18_run_capsule_and_manifest_paths(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            self.assertEqual(result["run_id"], "run_000001")
            self.assertIn("ouroboros_runs", result["run_artifact_root"])
            self.assertTrue(Path(result["run_manifest_path"]).exists())
            self.assertTrue(Path(result["run_manifest_raw_path"]).exists())
            w = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            self.assertEqual(w["run_capsule"]["run_id"], "run_000001")
            self.assertEqual(w["run_capsule"]["mode"], "cycle")
            self.assertIn("key_artifact_paths_relative", w)
            self.assertTrue(w["key_artifact_paths_relative"]["ouroboros_witness_diff"].endswith(".ouroboros.json"))

    def test_v18_two_cycle_runs_do_not_overwrite(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            base_cfg = {
                "max_revolutions": 1,
                "flux_budget": 1,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
            }
            cfg_path.write_text(json.dumps(base_cfg, indent=2), encoding="utf-8")
            r1 = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            r2 = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            self.assertEqual(r1["run_id"], "run_000001")
            self.assertEqual(r2["run_id"], "run_000002")
            self.assertNotEqual(r1["run_artifact_root"], r2["run_artifact_root"])
            self.assertTrue(Path(r1["ouroboros_witness_path"]).exists())
            self.assertTrue(Path(r2["ouroboros_witness_path"]).exists())

    def test_v18_run_capsule_disabled_flat_artifacts(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 1,
                        "flux_budget": 1,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 1, "min_improvement": 0.0, "no_improve_for": 2},
                        "run_capsule": {"enabled": False},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
        self.assertEqual(result["run_id"], "legacy_flat")
        p = Path(result["ouroboros_witness_path"])
        self.assertEqual(p.parent, resolved.artifact_dir)
        self.assertTrue("ouroboros_runs" not in result["run_artifact_root"])

    def test_diffable_witness_trace_omits_timestamps_and_raw_preserves_them(self):
        spell = self.runtime.load_spell(ROOT / "examples" / "primer_to_axioms.spell.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            result = self.runtime.execute_spell(
                spell,
                self.capabilities,
                {"publish"},
                False,
                ROOT / "policies" / "default.policy.json",
                out_dir,
            )
            diff_trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
            raw_trace = json.loads((out_dir / f"{spell.name}.trace.raw.json").read_text(encoding="utf-8"))
            # Diffable trace should not include timestamps/execution_id.
            self.assertNotIn("execution_id", diff_trace)
            self.assertNotIn("started_at", diff_trace)
            self.assertNotIn("ended_at", diff_trace)
            for event in diff_trace.get("events", []):
                self.assertNotIn("started_at", event)
                self.assertNotIn("ended_at", event)
            # Raw trace should preserve timing fields.
            self.assertIn("execution_id", raw_trace)
            self.assertIn("started_at", raw_trace)
            self.assertIn("ended_at", raw_trace)
            self.assertTrue(raw_trace.get("events"))
            self.assertIn("started_at", raw_trace["events"][0])
            self.assertIn("ended_at", raw_trace["events"][0])
            self.assertIn("capability_events", raw_trace)
            self.assertIsInstance(raw_trace["capability_events"], list)

    def test_diffable_trace_sanitizes_capability_targets(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        reviewed = self.runtime.build_review_bundle(resolved)
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            result = self.runtime.execute_spell(
                resolved.spell,
                self.capabilities,
                {"publish"},
                False,
                resolved.policy_path,
                out_dir,
                reviewed_bundle=reviewed,
            )
            diff_trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
            raw_trace = json.loads((out_dir / f"{resolved.spell.name}.trace.raw.json").read_text(encoding="utf-8"))
            # Raw may include machine-local paths; diffable must not contain Windows drive prefixes in structured capability events.
            raw_caps_text = json.dumps(raw_trace.get("capability_events", []))
            diff_caps_text = json.dumps(diff_trace.get("capability_events", []))
            self.assertTrue(raw_trace.get("capability_events"))
            self.assertNotRegex(diff_caps_text, r"[A-Za-z]:\\\\")
            self.assertNotRegex(diff_caps_text, r"\\\\\\\\")

    def test_fingerprint_repo_relpath_uses_posix_slashes(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        plan = self.runtime.build_plan_summary(resolved)
        files = plan["fingerprints"]["files"]
        self.assertTrue(files)
        for item in files:
            rel = item.get("repo_relpath")
            if rel is None:
                continue
            self.assertNotIn("\\\\", rel)
            # Top-level files like `spell.schema.json` legitimately contain no slashes.
            if "/" in rel:
                self.assertNotIn("\\\\", rel)

    def test_proof_timestamp_not_auto_injected(self):
        proof = self.runtime.normalize_proof({"validator": "x", "target": "y", "status": "passed"})
        self.assertIn("timestamp", proof)
        self.assertIsNone(proof["timestamp"])

    def test_diffable_trace_path_values_are_portable(self):
        spell = self.runtime.load_spell(ROOT / "examples" / "primer_to_axioms.spell.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            result = self.runtime.execute_spell(
                spell,
                self.capabilities,
                {"publish"},
                False,
                ROOT / "policies" / "default.policy.json",
                out_dir,
            )
            diff_trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
            # Only enforce portability on structured path-like surfaces (args), not freeform text output previews.
            for event in diff_trace.get("events", []):
                args = (event or {}).get("args", {})
                args_text = json.dumps(args)
                self.assertNotRegex(args_text, r"[A-Za-z]:\\\\")
                self.assertNotRegex(args_text, r"\\\\\\\\")

    def test_unresolved_dynamic_inputs_surface_in_review_bundle_and_attestation(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        bundle = self.runtime.build_review_bundle(resolved)
        summary = (
            (((bundle.get("fingerprints") or {}).get("input_manifest") or {}).get("classification") or {}).get("summary") or {}
        )
        self.assertIn("unresolved_dynamic_present", summary)
        self.assertTrue(summary["unresolved_dynamic_present"])
        attestation = self.runtime.compute_attestation(bundle, resolved, approvals={"publish"})
        self.assertIn(attestation["status"], ("partial", "mismatch"))

    def test_direct_primer_spell_succeeds_and_emits_proofs(self):
        spell = self.runtime.load_spell(ROOT / "examples" / "primer_to_axioms.spell.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.runtime.execute_spell(
                spell,
                self.capabilities,
                {"publish"},
                False,
                ROOT / "policies" / "default.policy.json",
                Path(tmpdir),
            )
            self.assertEqual(result["status"], "succeeded")
            self.assertGreaterEqual(result["proofs"]["passed"], 2)
            self.assertTrue(Path(result["trace_path"]).exists())
            self.assertTrue(Path(result["prov_path"]).exists())
            self.assertTrue(Path(result["scxml_path"]).exists())
            self.assertTrue(Path(result["proof_path"]).exists())

    def test_spellbook_entrypoint_succeeds_with_proof_summary(self):
        resolved = self.runtime.resolve_run_target(ROOT / "spellbooks" / "primer_codex", None, None, None)
        result = self.runtime.execute_spell(
            resolved.spell,
            self.capabilities,
            {"publish"},
            False,
            resolved.policy_path,
            resolved.artifact_dir,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertGreaterEqual(result["proofs"]["passed"], 4)
        self.assertEqual(result["proofs"]["failed"], 0)
        self.assertTrue((ROOT / "spellbooks" / "primer_codex" / "artifacts" / "primer_codex_v0_6.md").exists())
        self.assertTrue(Path(result["trace_path"]).exists())
        self.assertTrue(Path(result["prov_path"]).exists())
        self.assertTrue(Path(result["scxml_path"]).exists())
        self.assertTrue(Path(result["proof_path"]).exists())
        proof_doc = json.loads(Path(result["proof_path"]).read_text(encoding="utf-8"))
        self.assertEqual(proof_doc["passed"], result["proofs"]["passed"])

    def test_openapi_spell_fails_and_compensates_with_real_server(self):
        spell = self.runtime.load_spell(ROOT / "examples" / "openapi_ticket_then_fail.spell.json")
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            port = 8946
            spec_path = temp_root / "mock_issue_api.openapi.yaml"
            spec_text = (ROOT / "adapters" / "mock_issue_api.openapi.yaml").read_text(encoding="utf-8")
            spec_text = spec_text.replace("http://127.0.0.1:8942", f"http://127.0.0.1:{port}")
            spec_path.write_text(spec_text, encoding="utf-8")

            for step in spell.graph:
                if step.rune == "gate.openapi_call":
                    step.args["spec"] = str(spec_path)
            for step in spell.rollback:
                if step.rune == "gate.openapi_call":
                    step.args["spec"] = str(spec_path)

            env = os.environ.copy()
            env["AXIOMURGY_ISSUE_PORT"] = str(port)
            env["AXIOMURGY_ISSUE_DB"] = str(temp_root / "issues.json")
            server = subprocess.Popen(
                [sys.executable, str(ROOT / "adapters" / "mock_issue_server.py")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            try:
                ready = False
                for _ in range(20):
                    if server.poll() is not None:
                        raise RuntimeError("mock issue server exited before test execution")
                    try:
                        import requests
                        requests.get(f"http://127.0.0.1:{port}/tickets/does-not-exist", timeout=0.2)
                        ready = True
                        break
                    except Exception:
                        time.sleep(0.1)
                if not ready:
                    raise RuntimeError("mock issue server did not become ready in time")
                result = self.runtime.execute_spell(
                    spell,
                    self.capabilities,
                    {"create_ticket"},
                    False,
                    ROOT / "policies" / "default.policy.json",
                    temp_root,
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=2)

            self.assertEqual(result["status"], "failed")
            trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
            self.assertTrue(trace["compensations"])
            self.assertTrue(any(item["status"] == "compensated" for item in trace["compensations"]))
            self.assertIn("proofs", trace)
            self.assertTrue(Path(result["proof_path"]).exists())

    def test_v19_revolution_capsules_shape_distinct_artifact_roots(self):
        """v1.9: per-revolution dirs; traces do not overwrite; cycle result summarizes revolutions."""
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "max_revolutions": 3,
                        "flux_budget": 3,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 0.0, 3.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 3, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            raw = json.loads(Path(result["ouroboros_witness_raw_path"]).read_text(encoding="utf-8"))
            caps = witness["revolution_capsules"]
            self.assertTrue(len(caps) >= 2)
            for c in caps:
                self.assertIn("revolution_id", c)
                self.assertRegex(c["revolution_id"], r"^rev_\d{4}$")
                self.assertEqual(c["parent_run_id"], result["run_id"])
                self.assertIn("revolution_index", c)
                self.assertIn("executed", c)
                if c.get("executed"):
                    self.assertIsNotNone(c.get("artifact_root_relative"))
                    self.assertTrue(str(c["artifact_root_relative"]).startswith("revolutions/"))
                else:
                    self.assertIsNone(c.get("artifact_root_relative"))
                    self.assertIsNotNone(c.get("skipped_reason"))
            self.assertEqual(result["revolution_count_total"], len(caps))
            self.assertEqual(
                result["revolution_count_executed"] + result["revolution_count_skipped"],
                result["revolution_count_total"],
            )
            self.assertEqual(result["revolution_artifact_roots"], [x.get("artifact_root_relative") for x in caps])
            art = Path(result["run_artifact_root"])
            trace_files = sorted((art / "revolutions").glob("*/ouroboros_score_fixture.trace.json"))
            self.assertGreaterEqual(len(trace_files), 2)
            self.assertEqual(len(trace_files), len({str(p.resolve()) for p in trace_files}))
            for p in trace_files:
                self.assertIn("revolutions", p.parts)
            mf = json.loads(Path(result["run_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(mf["revolution_count_total"], witness["revolution_count_total"])
            self.assertTrue(mf.get("revolution_capsules"))
            diff_text = Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8")
            self.assertNotRegex(diff_text, r"[A-Za-z]:\\\\")
            self.assertIn("revolution_capsules", raw["run_capsule"])

    def test_v19_preflight_skips_have_capsules_without_execution_artifacts(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        reviewed = self.runtime.build_review_bundle(resolved)
        reviewed["capabilities"]["envelope"]["kinds"] = [
            k for k in reviewed["capabilities"]["envelope"]["kinds"] if k != "filesystem.write"
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved.artifact_dir = Path(tmpdir)
            cfg = {
                "max_revolutions": 2,
                "flux_budget": 2,
                "plateau_window": 2,
                "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                "mutation_target_allowlist": ["spell.inputs.score"],
                "mutation_targets": [{"path": "spell.inputs.score", "choices": [2.0, 3.0]}],
                "rollback_mode": "shadow_copy",
                "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
            }
            cfg_path = Path(tmpdir) / "cycle.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=reviewed,
                enforce_review_bundle=False,
            )
            art = Path(result["run_artifact_root"])
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            self.assertGreater(len(witness["preflight_skips"]), 0)
            self.assertEqual(witness["flux_attempts"], 0)
            for c in witness["revolution_capsules"]:
                self.assertFalse(c["executed"])
                self.assertIsNone(c.get("artifact_root_relative"))
            rev_root = art / "revolutions"
            if rev_root.exists():
                self.assertEqual(list(rev_root.iterdir()), [])

    def test_v19_run_capsule_disabled_still_emits_revolution_capsules(self):
        resolved = self.runtime.resolve_run_target(
            ROOT / "examples" / "ouroboros_score_fixture.spell.json", None, None, None
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            ad = Path(tmpdir)
            resolved.artifact_dir = ad
            cfg_path = ad / "cycle.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "run_capsule": {"enabled": False},
                        "max_revolutions": 2,
                        "flux_budget": 2,
                        "plateau_window": 2,
                        "target_metric": {"kind": "fixture_score", "path": "ouroboros_score.json"},
                        "mutation_target_allowlist": ["spell.inputs.score"],
                        "mutation_targets": [{"path": "spell.inputs.score", "choices": [1.0, 2.0]}],
                        "rollback_mode": "shadow_copy",
                        "stop_conditions": {"max_failures": 2, "min_improvement": 0.0, "no_improve_for": 2},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            result = self.runtime.ouroboros_chamber(
                resolved,
                cycle_config_path=cfg_path,
                approvals=set(),
                simulate=False,
                reviewed_bundle=None,
                enforce_review_bundle=False,
            )
            self.assertEqual(result["run_id"], "legacy_flat")
            witness = json.loads(Path(result["ouroboros_witness_path"]).read_text(encoding="utf-8"))
            self.assertIn("revolution_capsules", witness)
            self.assertTrue(witness["run_capsule"]["revolution_capsules"])
            executed = [c for c in witness["revolution_capsules"] if c.get("executed")]
            self.assertTrue(executed)
            art = Path(result["run_artifact_root"])
            for c in executed:
                rel = c["artifact_root_relative"]
                self.assertTrue((art / rel).is_dir())
                self.assertTrue((art / rel / "shadow.spell.json").exists())


if __name__ == "__main__":
    unittest.main()
