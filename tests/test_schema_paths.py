"""Regression: bundled schema/policy paths ship with the package (wheel-safe)."""

from __future__ import annotations

import unittest
from pathlib import Path

from axiomurgy.util import (
    DEFAULT_POLICY_PATH,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SPELLBOOK_SCHEMA_PATH,
    PACKAGE_ROOT,
    ROOT,
    sha256_file,
)


class TestSchemaPaths(unittest.TestCase):
    def test_root_is_parent_of_package(self):
        self.assertEqual(ROOT, PACKAGE_ROOT.parent)

    def test_bundled_contract_files_exist(self):
        self.assertTrue(DEFAULT_SCHEMA_PATH.is_file(), msg=str(DEFAULT_SCHEMA_PATH))
        self.assertTrue(DEFAULT_SPELLBOOK_SCHEMA_PATH.is_file(), msg=str(DEFAULT_SPELLBOOK_SCHEMA_PATH))
        self.assertTrue(DEFAULT_POLICY_PATH.is_file(), msg=str(DEFAULT_POLICY_PATH))

    def test_repo_root_contracts_match_bundled_when_present(self):
        """Editable installs keep repo-root JSON; wheel installs rely on bundled/ only."""
        repo = ROOT
        for name, bundled in (
            ("spell.schema.json", DEFAULT_SCHEMA_PATH),
            ("spellbook.schema.json", DEFAULT_SPELLBOOK_SCHEMA_PATH),
        ):
            with self.subTest(name=name):
                root_copy = repo / name
                if root_copy.is_file():
                    self.assertEqual(sha256_file(root_copy), sha256_file(bundled), msg=name)

        policy_root = repo / "policies" / "default.policy.json"
        if policy_root.is_file():
            self.assertEqual(sha256_file(policy_root), sha256_file(DEFAULT_POLICY_PATH))


if __name__ == "__main__":
    unittest.main()
