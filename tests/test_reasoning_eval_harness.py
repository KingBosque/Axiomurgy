"""Tests for the advisory reasoning efficacy harness (offline; no execution)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from axiomurgy.reasoning_eval.corpus import load_corpus, normalize_corpus_entries, resolve_corpus_spell_path
from axiomurgy.reasoning_eval.labels import load_labels
from axiomurgy.reasoning_eval.metrics import aggregate_metrics, compute_cross_mode_metrics, human_agreement_metrics
from axiomurgy.reasoning_eval.modes import EVAL_MODES, apply_eval_mode, mode_flags_snapshot
from axiomurgy.reasoning_eval.reports import build_markdown_report, write_report_artifacts
from axiomurgy.reasoning_eval.run import run_evaluation
from axiomurgy.util import ROOT


CORPUS_PATH = ROOT / "corpus" / "reasoning_eval_corpus.json"


class TestCorpus(unittest.TestCase):
    def test_corpus_loads_and_paths_resolve(self) -> None:
        doc = load_corpus(CORPUS_PATH)
        self.assertIn("spells", doc)
        entries = normalize_corpus_entries(doc)
        for e in entries:
            p = Path(e["_resolved_path"])
            self.assertTrue(p.is_file(), msg=str(p))

    def test_resolve_relative(self) -> None:
        p = resolve_corpus_spell_path({"path": "examples/primer_to_axioms.spell.json"})
        self.assertTrue(p.is_file())


class TestModes(unittest.TestCase):
    def test_all_modes_expected_flags(self) -> None:
        self.assertEqual(
            set(EVAL_MODES.keys()),
            {
                "baseline",
                "core_reasoning",
                "experimental_structure",
                "generation_only",
                "generation_ranked",
                "generation_ranked_wyrd",
            },
        )
        snap = mode_flags_snapshot("generation_ranked")
        self.assertEqual(snap["AXIOMURGY_REASONING_LULLIAN"], "1")
        self.assertIsNone(snap["AXIOMURGY_WYRD"])

    def test_apply_eval_mode_restores(self) -> None:
        os.environ["AXIOMURGY_REASONING"] = "0"
        with apply_eval_mode("core_reasoning"):
            self.assertEqual(os.environ.get("AXIOMURGY_REASONING"), "1")
        self.assertEqual(os.environ.get("AXIOMURGY_REASONING"), "0")


class TestHarnessRun(unittest.TestCase):
    def test_baseline_no_reasoning(self) -> None:
        doc = load_corpus(CORPUS_PATH)
        ent = normalize_corpus_entries(doc)[:1]
        out = run_evaluation(corpus_entries=ent, modes=["baseline"], artifact_root=Path(tempfile.mkdtemp()))
        r = out["modes"][0]["results"][0]
        self.assertFalse(r["reasoning_present"])

    def test_generation_modes_have_candidates_when_applicable(self) -> None:
        doc = load_corpus(CORPUS_PATH)
        ent = [e for e in normalize_corpus_entries(doc) if "openapi" in e["path"]][:1]
        out = run_evaluation(
            corpus_entries=ent,
            modes=["generation_only"],
            artifact_root=Path(tempfile.mkdtemp()),
        )
        r = out["modes"][0]["results"][0]
        self.assertGreaterEqual(r["candidate_count"], 0)
        self.assertTrue(r["reasoning_present"])

    def test_no_candidate_expected_honest(self) -> None:
        doc = load_corpus(CORPUS_PATH)
        ent = [e for e in normalize_corpus_entries(doc) if "readonly" in e["path"]][:1]
        out = run_evaluation(
            corpus_entries=ent,
            modes=["generation_only"],
            artifact_root=Path(tempfile.mkdtemp()),
        )
        r = out["modes"][0]["results"][0]
        self.assertEqual(r["candidate_count"], 0)
        self.assertTrue((r.get("expect") or {}).get("no_candidate_expected"))

    def test_metrics_deterministic(self) -> None:
        rows = [
            {"reasoning_present": True, "experimental_present": True, "candidate_count": 0, "output_shape": "full"},
            {"reasoning_present": True, "experimental_present": True, "candidate_count": 1, "output_shape": "full"},
        ]
        a = aggregate_metrics(rows)
        b = aggregate_metrics(rows)
        self.assertEqual(a, b)

    def test_report_writes(self) -> None:
        doc = {
            "eval_harness_version": "1.0.0",
            "modes": [{"mode": "baseline", "results": []}],
            "metrics_by_mode": {"baseline": aggregate_metrics([])},
        }
        md = build_markdown_report(doc)
        self.assertIn("Reasoning efficacy", md)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "out"
            paths = write_report_artifacts(doc, p)
            self.assertTrue(Path(paths["json"]).is_file())
            self.assertTrue(Path(paths["markdown"]).is_file())

    def test_labels_optional(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lp = Path(td) / "l.json"
            lp.write_text(
                json.dumps(
                    [{"spell_path": "examples/x.spell.json", "human_preferred_candidate_kind": "risk_reduction_variant"}]
                ),
                encoding="utf-8",
            )
            m = load_labels(lp)
            self.assertIn("examples/x.spell.json", m)

    def test_cross_mode_wyrd_delta(self) -> None:
        cm = compute_cross_mode_metrics(
            {
                "generation_ranked": [
                    {"spell_path": "/a", "preferred_candidate_kind": "subgoal_split"},
                ],
                "generation_ranked_wyrd": [
                    {"spell_path": "/a", "preferred_candidate_kind": "risk_reduction_variant"},
                ],
            }
        )
        self.assertEqual(cm["wyrd_preferred_kind_changed_count"], 1)

    @mock.patch("axiomurgy.vermyth_integration.run_vermyth_gate")
    def test_no_vermyth_during_eval(self, gate) -> None:
        doc = load_corpus(CORPUS_PATH)
        ent = normalize_corpus_entries(doc)[:1]
        run_evaluation(corpus_entries=ent, modes=["generation_ranked"], artifact_root=Path(tempfile.mkdtemp()))
        gate.assert_not_called()

    def test_spell_mtime_unchanged(self) -> None:
        doc = load_corpus(CORPUS_PATH)
        ent = normalize_corpus_entries(doc)[:1]
        p = Path(ent[0]["_resolved_path"])
        m0 = p.stat().st_mtime
        run_evaluation(corpus_entries=ent, modes=["generation_ranked"], artifact_root=Path(tempfile.mkdtemp()))
        self.assertEqual(p.stat().st_mtime, m0)

    def test_human_agreement(self) -> None:
        recs = [
            {"spell_path": "/abs/x", "preferred_candidate_kind": "subgoal_split"},
        ]
        labels = {"/abs/x": {"human_preferred_candidate_kind": "subgoal_split"}}
        h = human_agreement_metrics(recs, labels)
        self.assertEqual(h["human_labeled_spells"], 1)
        self.assertEqual(h["human_preferred_kind_agreement_rate"], 1.0)


class TestHarnessImports(unittest.TestCase):
    def test_package_import(self) -> None:
        import axiomurgy.reasoning_eval as re

        self.assertTrue(hasattr(re, "run_evaluation"))
