from __future__ import annotations

import copy
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import observe_decision_events as observation  # noqa: E402
import observed_runtime_benchmark as benchmark  # noqa: E402


class ObservedRuntimeBenchmarkTests(unittest.TestCase):
    def test_two_real_runtimes_compile_through_the_forge_core(self):
        manifest = benchmark.load_manifest()
        report = benchmark.evaluate(manifest)
        self.assertEqual(report["coverage"], {"status": "PASS", "runtimes": 2, "repositories": 2})
        self.assertEqual(report["status"], "PASS")
        self.assertEqual([item["events"] for item in report["runtimes"]], [4, 4])
        self.assertEqual([item["cases"] for item in report["runtimes"]], [2, 3])
        self.assertGreaterEqual(report["total_repeated_paths"], 1)
        self.assertTrue(all(all(item["checks"].values()) for item in report["runtimes"]))

    def test_tampered_artifact_provenance_is_rejected(self):
        manifest = benchmark.load_manifest()
        broken = copy.deepcopy(manifest)
        broken["runtimes"][0]["capture"]["event_sha256"] = "0" * 64
        with self.assertRaises(benchmark.ObservedRuntimeBenchmarkError):
            benchmark.validate_manifest(broken)

    def test_short_commit_and_unverified_baseline_are_rejected(self):
        manifest = benchmark.load_manifest()
        short_commit = copy.deepcopy(manifest)
        short_commit["runtimes"][0]["commit"] = "74636ac"
        with self.assertRaises(benchmark.ObservedRuntimeBenchmarkError):
            benchmark.validate_manifest(short_commit)
        failed_baseline = copy.deepcopy(manifest)
        failed_baseline["runtimes"][0]["baseline"]["status"] = "unknown"
        with self.assertRaises(benchmark.ObservedRuntimeBenchmarkError):
            benchmark.validate_manifest(failed_baseline)

    def test_private_reasoning_and_illegal_action_are_rejected(self):
        manifest = benchmark.load_manifest()
        runtime = manifest["runtimes"][0]
        events = observation.load_events(
            benchmark.FIXTURE_ROOT / runtime["capture"]["event_file"]
        )
        private = copy.deepcopy(events)
        private[0]["state_before"]["chain_of_thought"] = "must never be captured"
        with self.assertRaises(observation.ObservationError):
            observation.validate_events(private, runtime["opportunity"])
        illegal = copy.deepcopy(events)
        illegal[0]["available_actions"].append("delete_database")
        illegal[0]["selected_action"] = "delete_database"
        with self.assertRaises(observation.ObservationError):
            observation.validate_events(illegal, runtime["opportunity"])

    def test_one_runtime_is_insufficient_coverage(self):
        manifest = benchmark.load_manifest()
        incomplete = copy.deepcopy(manifest)
        incomplete["runtimes"] = incomplete["runtimes"][:1]
        self.assertEqual(benchmark.runtime_coverage(incomplete)["status"], "HOLD")

    def test_runtime_events_are_source_attributed_and_safe(self):
        manifest = benchmark.load_manifest()
        self.assertEqual(benchmark.verify_provenance(manifest), [])
        self.assertEqual(benchmark.verify_safety(manifest), [])


if __name__ == "__main__":
    unittest.main()
