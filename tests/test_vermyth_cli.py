"""CLI contracts for Vermyth: exit codes, stdout, baseline without env flags."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiomurgy.legacy import SpellValidationError, main

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SPELL = str(ROOT / "examples" / "primer_to_axioms.spell.json")


def _stripped_vermyth_env() -> dict[str, str]:
    env = os.environ.copy()
    for k in list(env.keys()):
        if k.startswith("AXIOMURGY_VERMYTH") or k.startswith("AXIOMURGY_CULTURE") or k == "VERMYTH_BASE_URL":
            del env[k]
    return env


def _run_cli(args: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [PY, str(ROOT / "axiomurgy.py"), *args],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or os.environ.copy(),
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestVermythCliBaseline(unittest.TestCase):
    def test_describe_no_vermyth_keys_without_flags(self) -> None:
        code, out, err = _run_cli([SPELL, "--describe"], env=_stripped_vermyth_env())
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(err.strip(), "")
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "describe")
        self.assertNotIn("culture", data)
        for k in ("vermyth_program_export", "vermyth_program_preview", "semantic_recommendations", "vermyth_gate"):
            self.assertNotIn(k, data)

    def test_plan_no_vermyth_keys_without_flags(self) -> None:
        code, out, err = _run_cli([SPELL, "--plan"], env=_stripped_vermyth_env())
        self.assertEqual(code, 0, msg=out + err)
        self.assertEqual(err.strip(), "")
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "plan")
        for k in ("vermyth_program_export", "vermyth_program_preview", "semantic_recommendations"):
            self.assertNotIn(k, data)


class TestVermythCliGateFailure(unittest.TestCase):
    def test_spell_validation_exit_1_and_error_stdout(self) -> None:
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("axiomurgy.vermyth_integration.run_vermyth_gate", side_effect=SpellValidationError("gate_fail")):
            with patch.object(sys, "stdout", out_buf), patch.object(sys, "stderr", err_buf):
                code = main([SPELL, "--simulate"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR:", out_buf.getvalue())
        self.assertIn("gate_fail", out_buf.getvalue())
        self.assertEqual(err_buf.getvalue(), "")


class TestVermythCliRouting(unittest.TestCase):
    def test_describe_does_not_call_run_vermyth_gate(self) -> None:
        with patch("axiomurgy.vermyth_integration.run_vermyth_gate") as g:
            code = main([SPELL, "--describe"])
            self.assertEqual(code, 0)
            g.assert_not_called()

    def test_simulate_calls_run_vermyth_gate_once(self) -> None:
        with patch("axiomurgy.vermyth_integration.run_vermyth_gate") as g:
            g.return_value = {"status": "skipped", "reason": "disabled"}
            code = main([SPELL, "--simulate"])
            self.assertEqual(code, 0)
            self.assertEqual(g.call_count, 1)

    def test_replay_branch_does_not_call_run_vermyth_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rev = Path(td)
            with patch("axiomurgy.legacy.replay_ouroboros_revolution") as rplay, patch(
                "axiomurgy.vermyth_integration.run_vermyth_gate"
            ) as g:
                rplay.return_value = {
                    "replay_status": "match",
                    "replay_summary_path": None,
                    "mode": "replay",
                    "axiomurgy_version": "x",
                    "original_revolution_id": "",
                    "source_run_id": "",
                    "source_revolution_dir": str(rev),
                    "compared_fields": [],
                    "mismatch_reasons": [],
                    "replay_summary_raw_path": None,
                }
                code = main([SPELL, "--replay-revolution-dir", str(rev)])
                self.assertEqual(code, 0)
                g.assert_not_called()


class TestVermythCliClosedPort(unittest.TestCase):
    """on_timeout allow: connection failure returns error record, execution may still exit 0."""

    @patch.dict(os.environ, {"AXIOMURGY_VERMYTH_BASE_URL": "http://127.0.0.1:1"}, clear=False)
    def test_gate_enabled_http_fails_returns_without_raise_when_on_timeout_allow(self) -> None:
        from axiomurgy.vermyth_integration import run_vermyth_gate

        pol = {
            "version": "2.0.0",
            "requires_approval": [],
            "deny": [],
            "vermyth_gate": {
                "enabled": True,
                "mode": "advisory",
                "timeout_ms": 200,
                "on_timeout": "allow",
                "on_incoherent": "allow",
            },
        }
        from axiomurgy.legacy import load_spell

        spell = load_spell(Path(SPELL))
        r = run_vermyth_gate(spell, pol)
        self.assertIn(r.get("status"), ("error",))

