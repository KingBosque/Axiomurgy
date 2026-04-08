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
        self.assertEqual(bundle["bundle_version"], "0.7")
        self.assertIn("environment", bundle)
        self.assertIn("describe", bundle)
        self.assertIn("lint", bundle)
        self.assertIn("plan", bundle)
        self.assertIn("approval_manifest", bundle)
        self.assertIn("fingerprints", bundle)
        self.assertIn("required", bundle["fingerprints"])

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
