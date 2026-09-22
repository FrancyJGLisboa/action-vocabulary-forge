import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import EXAMPLE, ROOT  # noqa: E402

import calibrate_thresholds  # noqa: E402
import decision_history as dh  # noqa: E402
import evaluate_decisions  # noqa: E402

Q = "next_validation_action"


def record(case_id, proposed, truth, confidence, *, abstained=False, action=None, outcome="ok", legal=("retry", "human_review"), blocked_reason=None):
    return {
        "case_id": case_id, "question_id": Q, "surface_id": "resolve_validation_failure", "state_id": "validation_failed",
        "proposed_action_id": proposed, "action_id": action or ("human_review" if abstained else proposed),
        "confidence": confidence, "abstained": abstained, "legal_actions": list(legal), "outcome": outcome,
        "blocked_reason": blocked_reason, "ground_truth_action_id": truth, "latency_ms": 12.0,
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }


def synthetic_log():
    """0.9-1.0: 100 records acc 0.98; 0.8-0.9: 50 records acc 0.6; 0.5-0.6: 50 records acc 0.3."""
    records = []
    for i in range(100):
        records.append(record(f"hi{i}", "retry", "retry" if i < 98 else "human_review", 0.95))
    for i in range(50):
        records.append(record(f"mid{i}", "retry", "retry" if i < 30 else "human_review", 0.85))
    for i in range(50):
        records.append(record(f"lo{i}", "retry", "retry" if i < 15 else "human_review", 0.55))
    return records


class HistoryTests(unittest.TestCase):
    def test_bins_and_suggestion(self):
        bins = dh.calibration_bins(synthetic_log())
        self.assertEqual(bins["0.9-1"]["n"], 100)
        self.assertAlmostEqual(bins["0.9-1"]["acc"], 0.98)
        self.assertAlmostEqual(bins["0.8-0.9"]["acc"], 0.6)
        self.assertEqual(dh.suggest_threshold(bins, 0.97), 0.9)
        self.assertEqual(dh.suggest_threshold(bins, 0.99), None)
        self.assertEqual(dh.suggest_threshold(bins, 0.8), 0.8)  # cumulative 128/150 = 0.853 >= 0.8; next bin 143/200 = 0.715 stops

    def test_split_is_deterministic_and_disjoint(self):
        records = synthetic_log()
        train, heldout = dh.split_cases(records, 0.3, 7)
        train2, heldout2 = dh.split_cases(records, 0.3, 7)
        self.assertEqual([r["case_id"] for r in train], [r["case_id"] for r in train2])
        self.assertEqual(len(train) + len(heldout), len(records))
        self.assertFalse({r["case_id"] for r in train} & {r["case_id"] for r in heldout})
        self.assertTrue(40 <= len(heldout) <= 80)

    def test_labels_csv_and_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "labels.csv"
            csv_path.write_text("# provenance header\ncase_id,question_id,ground_truth_action_id\nc1,q,retry\n")
            jsonl_path = Path(directory) / "labels.jsonl"
            jsonl_path.write_text(json.dumps({"case_id": "c2", "question_id": "q", "ground_truth_action_id": "human_review"}) + "\n")
            self.assertEqual(dh.read_labels(csv_path), {("c1", "q"): "retry"})
            self.assertEqual(dh.read_labels(jsonl_path), {("c2", "q"): "human_review"})
        labeled, dropped = dh.attach_labels([{"case_id": "c1", "question_id": "q"}, {"case_id": "zz", "question_id": "q"}], {("c1", "q"): "retry"})
        self.assertEqual((len(labeled), dropped), (1, 1))


class CalibrateTests(unittest.TestCase):
    def test_calibrate_groups(self):
        policy, rows = calibrate_thresholds.calibrate(EXAMPLE, synthetic_log(), min_accuracy=0.97, min_samples=30)
        self.assertEqual(policy["questions"][Q]["min_confidence"], 0.9)
        self.assertEqual(policy["questions"][Q]["actions"], {"retry": 0.9})
        self.assertEqual(policy["default_when_uncalibrated"], "abstain")
        small = [r for r in synthetic_log()][:10]
        policy, rows = calibrate_thresholds.calibrate(EXAMPLE, small, min_samples=30)
        self.assertNotIn("questions", policy)
        self.assertTrue(all(row["suggested"] is None for row in rows))

    def test_a_suggestion_below_the_floor_is_raised_to_it(self):
        """All-perfect bins on a small sample would otherwise leave the action ungated."""
        records = [record(f"lo{i}", "retry", "retry", 0.30) for i in range(20)]
        records += [record(f"hi{i}", "retry", "retry", 0.95) for i in range(20)]
        policy, rows = calibrate_thresholds.calibrate(EXAMPLE, records, min_samples=15)
        row = next(r for r in rows if r["action_id"] == "retry")
        self.assertEqual(row["suggested"], calibrate_thresholds.MIN_THRESHOLD)
        self.assertTrue(row["clamped"])
        self.assertEqual(policy["questions"][Q]["actions"]["retry"], calibrate_thresholds.MIN_THRESHOLD)
        self.assertEqual(policy["min_threshold"], calibrate_thresholds.MIN_THRESHOLD)
        policy, rows = calibrate_thresholds.calibrate(EXAMPLE, records, min_samples=15, min_threshold=0.0)
        self.assertEqual(next(r for r in rows if r["action_id"] == "retry")["suggested"], 0.0)

    def test_fallback_records_are_excluded(self):
        records = synthetic_log() + [record(f"fb{i}", "human_review", "human_review", 0.3) for i in range(40)]
        policy, rows = calibrate_thresholds.calibrate(EXAMPLE, records)
        self.assertEqual({row["action_id"] for row in rows}, {None, "retry"})

    def test_main_write_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(EXAMPLE, bundle)
            log = Path(directory) / "log.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in synthetic_log()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = calibrate_thresholds.main([str(bundle), "--log", str(log), "--heldout-fraction", "0", "--write"])
            self.assertEqual(code, 0)
            spec = yaml.safe_load((bundle / "jev_adapter_spec.yaml").read_text())
            self.assertEqual(spec["policy"]["questions"][Q]["actions"]["retry"], 0.9)
            self.assertEqual(spec["policy"]["min_confidence"], 0.85)  # existing global kept
            self.assertIn("calibrated_at", spec["policy"])
            self.assertIn("suggested", out.getvalue())


class EvaluateTests(unittest.TestCase):
    def run_eval(self, records, extra_args=()):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(EXAMPLE, bundle)
            log = Path(directory) / "log.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in records))
            out_json = Path(directory) / "eval.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = evaluate_decisions.main([str(bundle), "--log", str(log), "--all", "--json", str(out_json), *extra_args])
            return code, json.loads(out_json.read_text()), out.getvalue()

    def good_records(self):
        records = [record(f"r{i}", "retry", "retry", 0.95) for i in range(40)]
        records += [record(f"h{i}", "human_review", "human_review", 0.9) for i in range(5)]
        records += [record(f"a{i}", "retry", "human_review", 0.5, abstained=True) for i in range(5)]
        return records

    def test_known_metric_values_and_approve(self):
        code, result, text = self.run_eval(self.good_records())
        m = result["metrics"]
        self.assertEqual(m["boundary_accuracy"], {"count": 45, "total": 45, "pct": 1.0})
        self.assertEqual(m["abstention_accuracy"], {"count": 10, "total": 10, "pct": 1.0})
        self.assertEqual(m["jev_accuracy"]["count"], 45)
        self.assertEqual(m["illegal_action_rate"]["count"], 0)
        self.assertEqual(m["action_recall"], {"count": 2, "total": 2, "pct": 1.0})
        self.assertEqual(m["action_precision"]["count"], 2)
        self.assertEqual(m["surface_coverage"], {"count": 1, "total": 1, "pct": 1.0})
        self.assertEqual(m["replacement_rate"]["total"], 0)
        self.assertEqual(m["usage"], {"input_tokens": 250, "output_tokens": 50})
        self.assertTrue(result["approved"], result["failures"])
        self.assertEqual(code, 0)
        self.assertIn("RELEASE GATE: APPROVE", text)

    def test_one_illegal_record_holds(self):
        records = self.good_records() + [record("bad", "retry", "retry", 0.95, outcome="blocked", blocked_reason="preconditions failed for retry: ['retry_budget > 0']")]
        code, result, text = self.run_eval(records)
        self.assertEqual(code, 2)
        self.assertFalse(result["approved"])
        self.assertTrue(any("illegal actions" in f for f in result["failures"]))
        records = self.good_records() + [record("bad2", "retry", "retry", 0.95, legal=("human_review",))]
        code, result, _ = self.run_eval(records)
        self.assertEqual(result["metrics"]["illegal_action_rate"]["count"], 1)

    def test_low_boundary_accuracy_holds(self):
        records = [record(f"r{i}", "retry", "retry" if i % 2 else "human_review", 0.95) for i in range(40)]
        code, result, _ = self.run_eval(records)
        self.assertEqual(code, 2)
        self.assertTrue(any("boundary accuracy" in f for f in result["failures"]))

    def test_too_few_samples_holds(self):
        code, result, _ = self.run_eval(self.good_records()[:10])
        self.assertTrue(any("fewer than" in f for f in result["failures"]))

    def test_uncalibrated_policy_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(EXAMPLE, bundle)
            spec = yaml.safe_load((bundle / "jev_adapter_spec.yaml").read_text())
            spec["policy"] = {}
            (bundle / "jev_adapter_spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
            log = Path(directory) / "log.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in self.good_records()))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = evaluate_decisions.main([str(bundle), "--log", str(log), "--all"])
            self.assertEqual(code, 2)
            self.assertIn("no calibrated threshold", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class PortabilityTests(unittest.TestCase):
    """The README claims Python 3.11+; nested same-quote f-strings are 3.12+ only."""

    def test_every_script_parses_under_python_311(self):
        import ast
        import sys
        scripts = sorted((ROOT / "scripts").glob("*.py"))
        self.assertTrue(scripts)
        for path in scripts:
            with self.subTest(script=path.name):
                try:
                    ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))
                except SyntaxError as exc:
                    self.fail(f"{path.name} does not parse on Python 3.11: {exc}")
