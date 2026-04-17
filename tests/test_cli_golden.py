"""Golden-shape checks for major CLI JSON outputs (stable keys, no execution)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def _run_json(args: list[str]) -> dict:
    proc = subprocess.run(
        [PY, str(ROOT / "axiomurgy.py"), *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stdout)
    return json.loads(proc.stdout)


def _run_capture(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [PY, str(ROOT / "axiomurgy.py"), *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestCliGolden(unittest.TestCase):
    def test_describe_spellbook_has_expected_shape(self):
        code, out, err = _run_capture(["spellbooks/primer_codex", "--describe"])
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(err.strip(), "")
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "describe")
        self.assertEqual(data.get("kind"), "spellbook")
        self.assertIn("spellbook", data)
        self.assertIn("fingerprints", data)
        self.assertIn("capabilities", data)

    def test_lint_spellbook_ok(self):
        code, out, err = _run_capture(["spellbooks/primer_codex", "--lint"])
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(err.strip(), "")
        data = json.loads(out)
        self.assertEqual(data.get("kind"), "spellbook")
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("errors"), [])

    def test_plan_spellbook_has_manifest(self):
        manifest = ROOT / "spellbooks" / "primer_codex" / "artifacts" / "_golden_plan_manifest.json"
        code, out, err = _run_capture(
            [
                "spellbooks/primer_codex",
                "--plan",
                "--manifest-out",
                str(manifest),
            ]
        )
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(err.strip(), "")
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "plan")
        self.assertIn("manifest", data)
        self.assertIn("required_approvals", data)
        if manifest.exists():
            manifest.unlink()

    def test_verify_review_bundle_mismatch_exit_3(self):
        base = ROOT / "examples" / "primer_to_axioms.spell.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sp = tmp / "spell.spell.json"
            sp.write_text(base.read_text(encoding="utf-8"), encoding="utf-8")
            code0, bundle_out, err0 = _run_capture([str(sp), "--review-bundle"])
            self.assertEqual(code0, 0, msg=bundle_out + err0)
            bundle_path = tmp / "bundle.json"
            bundle_path.write_text(bundle_out, encoding="utf-8")
            sp.write_text(sp.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            code3, verify_out, err3 = _run_capture([str(sp), "--verify-review-bundle", str(bundle_path)])
            self.assertEqual(code3, 3, msg=verify_out + err3)
            self.assertEqual(err3.strip(), "")
            doc = json.loads(verify_out)
            self.assertEqual(doc.get("mode"), "verify")
            self.assertEqual(doc.get("status"), "mismatch")


if __name__ == "__main__":
    unittest.main()
