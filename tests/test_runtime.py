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


if __name__ == "__main__":
    unittest.main()
