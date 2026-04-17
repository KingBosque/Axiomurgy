"""Legacy vs native: extracted helpers must match re-exports (single implementation)."""

from __future__ import annotations

import unittest

import axiomurgy.describe as describe
import axiomurgy.execution as execution
import axiomurgy.legacy as legacy
import axiomurgy.planning as planning
import axiomurgy.review as review
import axiomurgy.util as util
from axiomurgy import fingerprint, proof
from axiomurgy.runes import MCPClient, REGISTRY as runes_registry, RuneRegistry


class TestNativeEquivalence(unittest.TestCase):
    def test_util_functions_are_shared_with_legacy(self):
        self.assertIs(legacy.utc_now, util.utc_now)
        self.assertIs(legacy.canonical_json, util.canonical_json)
        self.assertIs(legacy.sha256_bytes, util.sha256_bytes)
        self.assertIs(legacy.load_json, util.load_json)
        self.assertIs(legacy.normalize_paths_for_portability, util.normalize_paths_for_portability)

    def test_proof_functions_are_shared_with_legacy(self):
        self.assertIs(legacy.normalize_proof, proof.normalize_proof)
        self.assertIs(legacy.build_proof, proof.build_proof)
        self.assertIs(legacy.build_proof_summary, proof.build_proof_summary)

    def test_fingerprint_functions_are_shared_with_legacy(self):
        self.assertIs(legacy.classify_input_manifest, fingerprint.classify_input_manifest)
        self.assertIs(legacy.compute_spell_fingerprints, fingerprint.compute_spell_fingerprints)

    def test_registry_single_instance(self):
        self.assertIs(legacy.REGISTRY, runes_registry)
        self.assertIsInstance(legacy.REGISTRY, RuneRegistry)

    def test_mcp_client_is_native_runes(self):
        self.assertIs(legacy.MCPClient, MCPClient)

    def test_planning_module_shared_with_legacy(self):
        self.assertIs(legacy.load_spell, planning.load_spell)
        self.assertIs(legacy.compile_plan, planning.compile_plan)
        self.assertIs(legacy.build_plan_summary, planning.build_plan_summary)
        self.assertIs(legacy.resolve_run_target, planning.resolve_run_target)

    def test_describe_module_shared_with_legacy(self):
        self.assertIs(legacy.describe_target, describe.describe_target)
        self.assertIs(legacy.lint_target, describe.lint_target)
        self.assertIs(legacy.environment_metadata, describe.environment_metadata)

    def test_review_module_shared_with_legacy(self):
        self.assertIs(legacy.build_review_bundle, review.build_review_bundle)
        self.assertIs(legacy.compare_reviewed_bundle, review.compare_reviewed_bundle)

    def test_execution_module_shared_with_legacy(self):
        self.assertIs(legacy.RuneContext, execution.RuneContext)
        self.assertIs(legacy.execute_spell, execution.execute_spell)
        self.assertIs(legacy.run_step, execution.run_step)
        self.assertIs(legacy.evaluate_policy, execution.evaluate_policy)


if __name__ == "__main__":
    unittest.main()
