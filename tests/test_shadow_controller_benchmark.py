from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import shadow_controller_benchmark as benchmark  # noqa: E402


class ShadowControllerBenchmarkTests(unittest.TestCase):
    def test_public_code_derived_controller_is_finite_legal_and_non_executing(self):
        report = benchmark.evaluate()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["controller_stage"], "shadow_controller_ready")
        self.assertEqual(report["public_repository"], "https://github.com/openai/openai-agents-python")
        self.assertRegex(report["public_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(report["decider_calls"], 1)
        self.assertEqual(report["legal_actions_offered"], 2)
        self.assertEqual(report["illegal_actions_offered"], 0)
        self.assertEqual(report["terminal_stop"], "terminal_state")
        self.assertEqual(report["low_confidence_stop"], "low_confidence")
        self.assertEqual(report["binding_invocations"], 0)
        self.assertFalse(report["production_authority"])


if __name__ == "__main__":
    unittest.main()
