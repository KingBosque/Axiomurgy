"""Wyrd v1 append-only store, plan snapshot writes, and bounded hints."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from axiomurgy.planning import build_plan_summary, resolve_run_target
from axiomurgy.reasoning_bundle import (
    REASONING_VERSION,
    wyrd_persistence_enabled,
)
from axiomurgy.review import compare_reviewed_bundle, _attestation_allowlisted_path
from axiomurgy.wyrd.snapshot import append_reasoning_snapshot
from axiomurgy.wyrd.store import build_wyrd_hints, count_rows, wyrd_db_path

ROOT = Path(__file__).resolve().parents[1]


def _resolved(spell: Path):
    return resolve_run_target(spell, None, None, None)


def _env_reasoning_exp_wyrd() -> dict:
    return {
        "AXIOMURGY_REASONING": "1",
        "AXIOMURGY_REASONING_EXPERIMENTAL": "1",
        "AXIOMURGY_WYRD": "1",
    }


class TestWyrdVGating(unittest.TestCase):
    def tearDown(self) -> None:
        for k in ("AXIOMURGY_REASONING", "AXIOMURGY_REASONING_EXPERIMENTAL", "AXIOMURGY_WYRD"):
            os.environ.pop(k, None)

    def test_wyrd_flag(self) -> None:
        with mock.patch.dict(os.environ, {"AXIOMURGY_WYRD": "1"}, clear=False):
            self.assertTrue(wyrd_persistence_enabled())
        with mock.patch.dict(os.environ, {"AXIOMURGY_WYRD": "0"}, clear=False):
            self.assertFalse(wyrd_persistence_enabled())


class TestWyrdV1PlanSnapshot(unittest.TestCase):
    def tearDown(self) -> None:
        for k in ("AXIOMURGY_REASONING", "AXIOMURGY_REASONING_EXPERIMENTAL", "AXIOMURGY_WYRD"):
            os.environ.pop(k, None)

    @mock.patch.dict(os.environ, _env_reasoning_exp_wyrd(), clear=False)
    def test_plan_appends_nodes_and_edges(self) -> None:
        spell_path = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        ad = r.artifact_dir
        db = wyrd_db_path(ad)
        if db.is_file():
            db.unlink()
        plan = build_plan_summary(r)
        n, e = count_rows(ad)
        self.assertGreater(n, 0)
        self.assertGreater(e, 0)
        wh = plan["reasoning"]["experimental"]["wyrd_hints"]
        self.assertEqual(wh["kind"], "derived")
        self.assertIn("recent_nodes", wh)

    @mock.patch.dict(os.environ, _env_reasoning_exp_wyrd(), clear=False)
    def test_append_only_two_runs(self) -> None:
        spell_path = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        ad = r.artifact_dir
        db = wyrd_db_path(ad)
        if db.is_file():
            db.unlink()
        build_plan_summary(r)
        n1, e1 = count_rows(ad)
        build_plan_summary(r)
        n2, e2 = count_rows(ad)
        self.assertGreater(n2, n1)
        self.assertGreaterEqual(e2, e1)

    @mock.patch.dict(os.environ, {**_env_reasoning_exp_wyrd(), "AXIOMURGY_WYRD": "0"}, clear=False)
    def test_wyrd_hints_disabled_note_without_wyrd_flag(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        notes = plan["reasoning"]["experimental"]["wyrd_hints"]["consistency_notes"]
        self.assertIn("wyrd_disabled", notes)

    @mock.patch.dict(os.environ, {"AXIOMURGY_REASONING": "1", "AXIOMURGY_REASONING_EXPERIMENTAL": "1", "AXIOMURGY_WYRD": "0"}, clear=False)
    def test_experimental_without_wyrd_no_sql_write(self) -> None:
        spell_path = ROOT / "examples" / "primer_to_axioms.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        db = wyrd_db_path(r.artifact_dir)
        if db.is_file():
            db.unlink()
        build_plan_summary(r)
        self.assertFalse(db.is_file())


class TestWyrdV1KindsAndMapping(unittest.TestCase):
    def tearDown(self) -> None:
        for k in ("AXIOMURGY_REASONING", "AXIOMURGY_REASONING_EXPERIMENTAL", "AXIOMURGY_WYRD"):
            os.environ.pop(k, None)

    @mock.patch.dict(os.environ, _env_reasoning_exp_wyrd(), clear=False)
    def test_node_kinds_in_db(self) -> None:
        spell_path = ROOT / "examples" / "openapi_ticket_then_fail.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        dbp = wyrd_db_path(r.artifact_dir)
        if dbp.is_file():
            dbp.unlink()
        build_plan_summary(r)
        conn = sqlite3.connect(str(dbp))
        try:
            kinds = {row[0] for row in conn.execute("SELECT DISTINCT kind FROM wyrd_nodes").fetchall()}
        finally:
            conn.close()
        self.assertIn("telos", kinds)
        self.assertIn("governor_tradeoff", kinds)
        self.assertIn("dialectic_episode", kinds)
        self.assertIn("correspondence_cluster", kinds)
        self.assertIn("friction_bottleneck", kinds)

    @mock.patch.dict(os.environ, _env_reasoning_exp_wyrd(), clear=False)
    def test_witness_ref_when_witness_records(self) -> None:
        spell_path = ROOT / "examples" / "inbox_triage.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        r = _resolved(spell_path)
        dbp = wyrd_db_path(r.artifact_dir)
        if dbp.is_file():
            dbp.unlink()
        build_plan_summary(r)
        conn = sqlite3.connect(str(dbp))
        try:
            kinds = {row[0] for row in conn.execute("SELECT DISTINCT kind FROM wyrd_nodes").fetchall()}
        finally:
            conn.close()
        self.assertIn("witness_ref", kinds)


class TestWyrdV1SoftFail(unittest.TestCase):
    @mock.patch.dict(os.environ, _env_reasoning_exp_wyrd(), clear=False)
    @mock.patch("axiomurgy.wyrd.store.append_graph_snapshot", side_effect=OSError("disk"))
    def test_storage_failure_plan_still_ok(self, _patch) -> None:
        spell_path = ROOT / "examples" / "calibration" / "readonly_probe_low_risk.spell.json"
        if not spell_path.is_file():
            self.skipTest("example missing")
        plan = build_plan_summary(_resolved(spell_path))
        self.assertIn("reasoning", plan)
        self.assertEqual(plan["reasoning"]["axiomurgy_reasoning_version"], REASONING_VERSION)


class TestWyrdV1Attestation(unittest.TestCase):
    def test_experimental_subtree_still_allowlisted(self) -> None:
        self.assertTrue(_attestation_allowlisted_path("plan.reasoning.experimental.wyrd_hints.kind"))

    def test_compare_ignores_reasoning_drift(self) -> None:
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
            "plan": {"reasoning": {"experimental": {"wyrd_hints": {"x": 1}}}},
            "describe": {},
        }
        current = dict(reviewed)
        current["plan"] = dict(reviewed["plan"])
        current["plan"]["reasoning"] = {"experimental": {"wyrd_hints": {"x": 2}}}
        cmp = compare_reviewed_bundle(reviewed, current)
        self.assertEqual(cmp["status"], "exact")


class TestWyrdV1Vermyth(unittest.TestCase):
    @mock.patch.dict(os.environ, _env_reasoning_exp_wyrd(), clear=False)
    @mock.patch("axiomurgy.vermyth_integration.run_vermyth_gate")
    def test_describe_no_vermyth_gate(self, gate) -> None:
        from axiomurgy.legacy import main

        spell = str(ROOT / "examples" / "primer_to_axioms.spell.json")
        if not Path(spell).is_file():
            self.skipTest("example missing")
        code = main([spell, "--describe"])
        self.assertEqual(code, 0)
        gate.assert_not_called()