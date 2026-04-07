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
        self.assertTrue((ROOT / "spellbooks" / "primer_codex" / "artifacts" / "primer_codex_v0_5.md").exists())
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
