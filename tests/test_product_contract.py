"""Executable acceptance contract for the final Forge product target."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_product_contract as contract  # noqa: E402


class ProductContractTests(unittest.TestCase):
    def test_product_target_contains_every_required_contract_section(self):
        result = contract.validate_target(ROOT / "docs" / "product-target.md")
        self.assertEqual(result, [])

    def test_scenario_matrix_covers_every_required_input_archetype(self):
        matrix = contract.load_matrix(contract.DEFAULT_MATRIX)
        result = contract.validate_matrix(matrix)
        self.assertEqual(result, [])
        self.assertEqual(len(matrix["scenarios"]), 6)

    def test_all_product_scenarios_meet_the_declared_contract(self):
        matrix = contract.load_matrix(contract.DEFAULT_MATRIX)
        with tempfile.TemporaryDirectory() as directory:
            results = contract.evaluate_matrix(matrix, Path(directory))
        self.assertEqual([item["status"] for item in results], ["pass"] * 6)

    def test_target_validator_rejects_an_aspirational_but_unmeasurable_document(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.md"
            target.write_text("# Build a universal decision system\n", encoding="utf-8")
            missing = contract.validate_target(target)
        self.assertIn("product-contract:v1", missing)
        self.assertIn("## Acceptance corpus", missing)


if __name__ == "__main__":
    unittest.main()
