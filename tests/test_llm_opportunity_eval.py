import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
PATH = ROOT / "scripts" / "evaluate_llm_opportunity_scanner.py"
spec = importlib.util.spec_from_file_location("scanner_eval", PATH)
scanner_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner_eval)


class ScannerEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = scanner_eval.load_cases(scanner_eval.DEFAULT_CASES)

    def test_frozen_set_has_every_label_and_at_least_twenty_four_cases(self):
        self.assertGreaterEqual(len(self.cases), 24)
        self.assertEqual({case["label"] for case in self.cases}, scanner_eval.LABELS)
        self.assertTrue(any(case.get("source_reference") for case in self.cases))
        self.assertTrue(all(case["expected_lines"] for case in self.cases if case["label"] in scanner_eval.ELIGIBLE_LABELS))
        self.assertTrue(all(not case["expected_lines"] for case in self.cases if case["label"] not in scanner_eval.ELIGIBLE_LABELS))

    def test_scanner_meets_release_gate_on_exact_call_sites(self):
        report = scanner_eval.evaluate_cases(self.cases)
        self.assertGreaterEqual(report["precision"], 0.9)
        self.assertGreaterEqual(report["recall"], 0.8)
        self.assertEqual(report["false_positive"], 0)
        self.assertEqual(report["false_negative"], 0)
        self.assertTrue(report["approved"])

    def test_gate_holds_when_expected_call_sites_are_missed(self):
        cases = copy.deepcopy(self.cases)
        positive = next(case for case in cases if case["label"] in scanner_eval.ELIGIBLE_LABELS)
        positive["source"] = "value = 1\n"
        report = scanner_eval.evaluate_cases(cases, min_recall=1.0)
        self.assertFalse(report["approved"])
        self.assertGreater(report["false_negative"], 0)

    def test_invalid_thresholds_are_rejected(self):
        for value in (-0.1, 1.1, True):
            with self.subTest(value=value), self.assertRaises(scanner_eval.EvaluationError):
                scanner_eval.evaluate_cases(self.cases, min_precision=value)


if __name__ == "__main__":
    unittest.main()
