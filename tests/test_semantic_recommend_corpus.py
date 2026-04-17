"""Corpus file for semantic recommendation calibration is loadable and paths exist."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from axiomurgy.planning import load_spell

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs" / "data" / "semantic_recommend_corpus.json"


class TestSemanticRecommendCorpus(unittest.TestCase):
    def test_corpus_json_loads(self) -> None:
        raw = json.loads(CORPUS.read_text(encoding="utf-8"))
        self.assertIn("spells", raw)
        self.assertGreaterEqual(len(raw["spells"]), 8)

    def test_all_spell_paths_exist_and_load(self) -> None:
        raw = json.loads(CORPUS.read_text(encoding="utf-8"))
        for row in raw["spells"]:
            p = ROOT / row["path"]
            self.assertTrue(p.is_file(), msg=f"missing {p}")
            load_spell(p)
