"""Contract tests: root shim and package re-exports stay stable for loaders and CLI."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_root_shim():
    spec = importlib.util.spec_from_file_location("axiomurgy_root_shim", ROOT / "axiomurgy.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPackageExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shim = _load_root_shim()
        import axiomurgy as pkg

        cls.pkg = pkg

    def test_root_shim_exposes_core_entrypoints(self):
        for name in (
            "VERSION",
            "ROOT",
            "load_spell",
            "compile_plan",
            "execute_spell",
            "describe_target",
            "lint_target",
            "build_review_bundle",
            "main",
            "parse_args",
            "REGISTRY",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(self.shim, name), f"root shim missing {name}")

    def test_root_shim_private_helpers_for_tests(self):
        self.assertTrue(hasattr(self.shim, "_admissibility_status_rank"))
        self.assertTrue(hasattr(self.shim, "_revolution_dir_from_run_manifest"))

    def test_package_import_axiomurgy(self):
        import axiomurgy

        self.assertTrue(hasattr(axiomurgy, "execute_spell"))
        self.assertTrue(hasattr(axiomurgy, "legacy"))

    def test_native_modules_are_importable(self):
        from axiomurgy import fingerprint, proof, runes, util

        self.assertTrue(util.ROOT.exists())
        self.assertTrue(hasattr(runes, "REGISTRY"))
        self.assertTrue(hasattr(proof, "normalize_proof"))
        self.assertTrue(hasattr(fingerprint, "compute_spell_fingerprints"))

    def test_submodules_import_graph_deterministic(self):
        """Guard against circular imports / missing modules after package splits."""
        import importlib

        order = [
            "axiomurgy.core",
            "axiomurgy.util",
            "axiomurgy.proof",
            "axiomurgy.fingerprint",
            "axiomurgy.runes",
            "axiomurgy.planning",
            "axiomurgy.vermyth_export",
            "axiomurgy.vermyth_integration",
            "axiomurgy.adapters.vermyth_http",
            "axiomurgy.culture",
            "axiomurgy.describe",
            "axiomurgy.review",
            "axiomurgy.execution",
            "axiomurgy.ouroboros",
            "axiomurgy.cli",
            "axiomurgy.legacy",
        ]
        for name in order:
            with self.subTest(module=name):
                importlib.import_module(name)
        importlib.import_module("axiomurgy.__main__")


if __name__ == "__main__":
    unittest.main()
