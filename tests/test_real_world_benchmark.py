from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import real_world_benchmark as benchmark  # noqa: E402
import scan_decision_opportunities as scanner  # noqa: E402


class RealWorldBenchmarkTests(unittest.TestCase):
    def test_frozen_corpus_has_provenance_and_reports_pinned_metrics(self):
        manifest = benchmark.load_manifest()
        report = benchmark.evaluate(manifest)
        self.assertEqual(len(manifest["entries"]), 19)
        self.assertEqual(report["confusion"], {"true_positive": 10, "false_negative": 0, "false_positive": 0, "true_negative": 9})
        self.assertAlmostEqual(report["precision"], 1.0)
        self.assertAlmostEqual(report["recall"], 1.0)
        self.assertAlmostEqual(report["specificity"], 1.0)
        self.assertEqual(report["synthetic"]["total"], 8)
        self.assertEqual(report["external"]["total"], 11)
        self.assertEqual(report["external"]["positive"], 5)
        self.assertEqual(report["external"]["negative"], 6)
        self.assertEqual(len(report["external"]["positive_llm_ecosystems"]), 2)
        self.assertEqual(report["gate"], "PASS")
        self.assertEqual(report["product_readiness"], "PASS")
        self.assertEqual(report["known_gaps"], [])

    def test_adversarial_lists_prose_and_open_generation_stay_unbounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "list.md").write_text("Options include search, browser, terminal.\n", encoding="utf-8")
            (root / "prose.md").write_text("The report summarizes accept, revise, and escalate examples.\n", encoding="utf-8")
            (root / "generate.py").write_text("def f(model):\n    return model.generate('Write any answer you think is useful.')\n", encoding="utf-8")
            result = scanner.scan([root], system_id="adversarial")
            self.assertEqual([item["type"] for item in result["opportunities"]], ["open_generation"])
            self.assertNotIn("bounded_semantic_decision", [item["type"] for item in result["opportunities"]])

    def test_malformed_provenance_is_rejected(self):
        manifest = benchmark.load_manifest()
        broken = copy.deepcopy(manifest)
        broken["entries"][0]["source_url"] = "https://example.com/not-github"
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.validate_manifest(broken)

    def test_unprovenanced_entry_is_rejected(self):
        manifest = benchmark.load_manifest()
        broken = copy.deepcopy(manifest)
        broken["entries"][0]["commit"] = "abc"
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.validate_manifest(broken)


if __name__ == "__main__":
    unittest.main()
