from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import binding_verification_benchmark as benchmark  # noqa: E402


class BindingVerificationBenchmarkTests(unittest.TestCase):
    def test_public_code_derived_controlled_binding_reaches_shadow_only(self):
        report = benchmark.evaluate()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["public_repository"], "https://github.com/openai/openai-agents-python")
        self.assertRegex(report["public_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(report["controlled_harness_executed"])
        self.assertFalse(report["forge_executed_target"])
        self.assertEqual(report["successful_observations"], 1)
        self.assertEqual(report["negative_observations"], 1)
        self.assertEqual(report["verified_shadow_bindings"], 1)
        self.assertEqual(report["executable_bindings"], 0)
        self.assertFalse(report["production_authority"])


if __name__ == "__main__":
    unittest.main()
