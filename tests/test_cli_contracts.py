"""CLI exit codes, stdout/stderr split, and invocation parity (shim, -m, import)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SHIM = str(ROOT / "axiomurgy.py")


def _env_for_module_run() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return env


def _run_shim(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, SHIM, *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _run_module(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "-m", "axiomurgy", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env_for_module_run(),
    )


def _run_import_main(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    arg_repr = repr(args)
    code = (
        "import sys\n"
        "from axiomurgy.cli import main\n"
        f"sys.exit(main({arg_repr}))\n"
    )
    return subprocess.run(
        [PY, "-c", code],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env_for_module_run(),
    )


class TestCliContracts(unittest.TestCase):
    def _assert_invocation_parity(self, args: list[str], *, expected_code: int, stdout_sub: str | None = None) -> None:
        for name, runner in (
            ("shim", _run_shim),
            ("module", _run_module),
            ("import", _run_import_main),
        ):
            with self.subTest(mode=name, args=args):
                proc = runner(args, cwd=ROOT)
                self.assertEqual(proc.returncode, expected_code, msg=f"{name} stdout={proc.stdout!r} stderr={proc.stderr!r}")
                if stdout_sub is not None:
                    self.assertIn(stdout_sub, proc.stdout)
                    # Current contract: CLI errors go to stdout, not stderr.
                    self.assertEqual(proc.stderr.strip(), "")

    def test_missing_target_exit_2(self):
        missing = ROOT / "does_not_exist_spellbook_xyz"
        self._assert_invocation_parity(
            [str(missing), "--describe"],
            expected_code=2,
            stdout_sub="ERROR:",
        )

    def test_invalid_spellbook_json_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = Path(tmp) / "badbook"
            sb.mkdir()
            (sb / "spellbook.json").write_text("{", encoding="utf-8")
            self._assert_invocation_parity(
                [str(sb), "--describe"],
                expected_code=1,
                stdout_sub="ERROR:",
            )

    def test_malformed_spell_json_exit_1(self):
        """Schema/load failures raise before JSON result; CLI exits 1 with ERROR on stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.spell.json"
            bad.write_text("{ not json", encoding="utf-8")
            self._assert_invocation_parity(
                [str(bad), "--describe"],
                expected_code=1,
                stdout_sub="ERROR:",
            )

    def test_lint_malformed_spell_json_exit_0_with_ok_false(self):
        """Lint reports structured errors without raising; exit code stays 0 (current contract)."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.spell.json"
            bad.write_text("{ not json", encoding="utf-8")
            proc = _run_shim([str(bad), "--lint"], cwd=ROOT)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stderr.strip(), "")
            body = json.loads(proc.stdout)
            self.assertFalse(body.get("ok"))
            self.assertTrue(any(e.get("code") == "json" for e in body.get("errors") or []))

    def test_policy_denial_execution_result_exit_0(self):
        """Policy denial yields JSON with status failed; CLI still exits 0 (result on stdout)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            policy = tmp / "deny.json"
            policy.write_text(
                json.dumps({"deny": [{"rune": ["mirror.read"], "reason": "CLI policy denial test."}]}),
                encoding="utf-8",
            )
            spell = tmp / "policy_cli.spell.json"
            spell.write_text(
                json.dumps(
                    {
                        "spell": "policy_cli",
                        "intent": "test",
                        "inputs": {"text": "hello"},
                        "constraints": {},
                        "graph": [
                            {
                                "id": "s1",
                                "rune": "mirror.read",
                                "effect": "transform",
                                "args": {"input": "$inputs.text"},
                            }
                        ],
                        "rollback": [],
                        "witness": {"record": False},
                    }
                ),
                encoding="utf-8",
            )
            args = [
                str(spell),
                "--policy",
                str(policy),
                "--artifact-dir",
                str(tmp / "out"),
                "--simulate",
            ]
            for runner in (_run_shim, _run_module, _run_import_main):
                proc = runner(args, cwd=ROOT)
                self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
                self.assertEqual(proc.stderr.strip(), "")
                body = json.loads(proc.stdout)
                self.assertEqual(body.get("status"), "failed")
                self.assertIn("CLI policy denial test.", body.get("error") or "")

    def test_rune_execution_failure_result_exit_0(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            spell = tmp / "rune_fail.spell.json"
            spell.write_text(
                json.dumps(
                    {
                        "spell": "rune_fail",
                        "intent": "test",
                        "inputs": {},
                        "constraints": {},
                        "graph": [
                            {
                                "id": "s1",
                                "rune": "mirror.read",
                                "effect": "transform",
                                "args": {},
                            }
                        ],
                        "rollback": [],
                        "witness": {"record": False},
                    }
                ),
                encoding="utf-8",
            )
            args = [
                str(spell),
                "--policy",
                str(ROOT / "policies" / "default.policy.json"),
                "--artifact-dir",
                str(tmp / "out"),
                "--simulate",
            ]
            proc = _run_shim(args, cwd=ROOT)
            self.assertEqual(proc.returncode, 0)
            body = json.loads(proc.stdout)
            self.assertEqual(body.get("status"), "failed")
            self.assertIn("mirror.read requires", body.get("error") or "")


class TestCliCwdIndependence(unittest.TestCase):
    """CLI resolves absolute spell paths when cwd is not the repository root."""

    def test_describe_primer_codex_absolute_path_non_repo_cwd(self):
        target = (ROOT / "spellbooks" / "primer_codex").resolve()
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_shim([str(target), "--describe"], cwd=Path(tmp))
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            out = json.loads(proc.stdout)
            self.assertEqual(out.get("mode"), "describe")


if __name__ == "__main__":
    unittest.main()
