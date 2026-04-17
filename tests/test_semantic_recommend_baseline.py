"""Tests for compatibility baseline helpers (loaded from scripts/eval_semantic_recommendations.py)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_eval_script():
    path = ROOT / "scripts" / "eval_semantic_recommendations.py"
    spec = importlib.util.spec_from_file_location("eval_semantic_recommendations", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCompareToBaseline(unittest.TestCase):
    def test_allow_axiomurgy_sha_drift_still_enforces_vermyth(self) -> None:
        mod = _load_eval_script()
        baseline = {
            "axiomurgy_git": "old_axiomurgy",
            "vermyth_git": "pinned_vermyth",
            "expectations": [],
        }
        ok, fails = mod.compare_to_baseline(
            baseline,
            current_meta={"axiomurgy_git": "new_axiomurgy", "vermyth_git": "wrong_vermyth"},
            runs=[],
            allow_sha_drift=False,
            allow_axiomurgy_sha_drift=True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("vermyth_git drift" in f for f in fails))

    def test_pass_when_probe_matches_baseline(self) -> None:
        mod = _load_eval_script()
        baseline = {
            "baseline_version": 1,
            "captured_at": "2026-01-01T00:00:00Z",
            "axiomurgy_git": "aaa",
            "vermyth_git": "bbb",
            "expectations": [
                {
                    "spell_path": "examples/x.spell.json",
                    "expected_top_bundle_id": "axiomurgy_inbox_triage",
                    "expected_match_kind": "exact",
                    "forbidden_top_bundle_ids": [],
                    "recommendations_fingerprint": None,
                }
            ],
        }
        runs = [
            {
                "spell_path": "examples/x.spell.json",
                "recommendations": [
                    {
                        "bundle_id": "axiomurgy_inbox_triage",
                        "match_kind": "exact",
                        "strength": 0.9,
                    }
                ],
            }
        ]
        ok, fails = mod.compare_to_baseline(
            baseline,
            current_meta={"axiomurgy_git": "aaa", "vermyth_git": "bbb"},
            runs=runs,
            allow_sha_drift=False,
        )
        self.assertTrue(ok)
        self.assertEqual(fails, [])

    def test_fail_top_bundle_mismatch(self) -> None:
        mod = _load_eval_script()
        baseline = {
            "expectations": [
                {
                    "spell_path": "examples/x.spell.json",
                    "expected_top_bundle_id": "axiomurgy_inbox_triage",
                    "expected_match_kind": "exact",
                    "forbidden_top_bundle_ids": [],
                    "recommendations_fingerprint": None,
                }
            ],
        }
        runs = [
            {
                "spell_path": "examples/x.spell.json",
                "recommendations": [{"bundle_id": "other", "match_kind": "exact", "strength": 0.9}],
            }
        ]
        ok, fails = mod.compare_to_baseline(
            baseline,
            current_meta={"axiomurgy_git": None, "vermyth_git": None},
            runs=runs,
            allow_sha_drift=True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("top bundle" in f for f in fails))

    def test_negative_forbidden_top(self) -> None:
        mod = _load_eval_script()
        baseline = {
            "expectations": [
                {
                    "spell_path": "examples/calibration/y.spell.json",
                    "expected_top_bundle_id": None,
                    "expected_match_kind": None,
                    "forbidden_top_bundle_ids": ["axiomurgy_inbox_triage"],
                    "recommendations_fingerprint": None,
                }
            ],
        }
        runs = [
            {
                "spell_path": "examples/calibration/y.spell.json",
                "recommendations": [
                    {"bundle_id": "axiomurgy_inbox_triage", "match_kind": "exact", "strength": 0.9}
                ],
            }
        ]
        ok, fails = mod.compare_to_baseline(
            baseline,
            current_meta={},
            runs=runs,
            allow_sha_drift=True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("negative control" in f for f in fails))

    def test_fingerprint_mismatch(self) -> None:
        mod = _load_eval_script()
        recs = [{"bundle_id": "axiomurgy_inbox_triage", "version": None, "match_kind": "exact", "strength": 0.9, "target_skill": None}]
        fp = mod.fingerprint_from_normalized_recs(recs)
        baseline = {
            "expectations": [
                {
                    "spell_path": "examples/x.spell.json",
                    "expected_top_bundle_id": "axiomurgy_inbox_triage",
                    "expected_match_kind": "exact",
                    "forbidden_top_bundle_ids": [],
                    "recommendations_fingerprint": fp,
                }
            ],
        }
        runs = [
            {
                "spell_path": "examples/x.spell.json",
                "recommendations": [
                    {
                        "bundle_id": "axiomurgy_inbox_triage",
                        "version": None,
                        "match_kind": "exact",
                        "strength": 0.1,
                        "target_skill": None,
                    }
                ],
            }
        ]
        ok, fails = mod.compare_to_baseline(
            baseline,
            current_meta={},
            runs=runs,
            allow_sha_drift=True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("fingerprint" in f for f in fails))

    def test_expectations_from_corpus_roundtrip(self) -> None:
        mod = _load_eval_script()
        corpus_path = ROOT / "docs" / "data" / "semantic_recommend_corpus.json"
        if not corpus_path.is_file():
            self.skipTest("corpus missing")
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        exp = mod.expectations_from_corpus(corpus)
        self.assertGreaterEqual(len(exp), 3)
        negs = [e for e in exp if e["expected_top_bundle_id"] is None]
        self.assertTrue(negs)


if __name__ == "__main__":
    unittest.main()
