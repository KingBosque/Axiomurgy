"""Receipt sidecar and culture describe block (opt-in only)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiomurgy.describe import describe_target
from axiomurgy.execution import execute_spell
from axiomurgy.planning import resolve_run_target

ROOT = Path(__file__).resolve().parents[1]
SPELL = ROOT / "examples" / "primer_to_axioms.spell.json"


class TestVermythReceipt(unittest.TestCase):
    def test_no_receipt_file_when_emit_false(self) -> None:
        from axiomurgy.legacy import load_spell

        spell = load_spell(SPELL)
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        with tempfile.TemporaryDirectory() as td:
            ad = Path(td)
            r = execute_spell(
                spell,
                ["approve", "read", "reason", "simulate", "transform", "verify", "write"],
                {"publish"},
                True,
                policy_path,
                ad,
                vermyth_receipt_emit=False,
            )
            rp = Path(r.get("vermyth_receipt_path") or "")
            if rp:
                self.assertFalse(rp.is_file())
            sidecar = ad / f"{spell.name}.vermyth_receipt.json"
            self.assertFalse(sidecar.is_file())

    def test_receipt_path_when_emit_true(self) -> None:
        from axiomurgy.legacy import load_spell

        spell = load_spell(SPELL)
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        with tempfile.TemporaryDirectory() as td:
            ad = Path(td)
            r = execute_spell(
                spell,
                ["approve", "read", "reason", "simulate", "transform", "verify", "write"],
                {"publish"},
                True,
                policy_path,
                ad,
                vermyth_receipt_emit=True,
            )
            self.assertIn("vermyth_receipt_path", r)
            p = Path(r["vermyth_receipt_path"])
            self.assertTrue(p.is_file(), msg=f"missing {p}")


class TestCultureDescribe(unittest.TestCase):
    def test_culture_absent_when_disabled(self) -> None:
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        resolved = resolve_run_target(SPELL, None, policy_path, ROOT / "artifacts")
        with patch.dict(os.environ, {"AXIOMURGY_CULTURE": "0"}, clear=False):
            d = describe_target(resolved)
            self.assertNotIn("culture", d)

    def test_culture_present_when_enabled(self) -> None:
        policy_path = ROOT / "axiomurgy" / "bundled" / "policies" / "default.policy.json"
        resolved = resolve_run_target(SPELL, None, policy_path, ROOT / "artifacts")
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "culture.sqlite3"
            with patch.dict(
                os.environ,
                {"AXIOMURGY_CULTURE": "1", "AXIOMURGY_CULTURE_DB": str(db)},
                clear=False,
            ):
                d = describe_target(resolved)
            self.assertIn("culture", d)
            self.assertTrue(d["culture"].get("enabled") is True)

