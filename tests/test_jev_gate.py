import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401  (puts scripts/ on sys.path)

import jev_gate  # noqa: E402


def decisions():
    """High-confidence answers are right; low-confidence ones are cases a person should take."""
    rows = []
    for i in range(120):
        rows.append({"case_id": f"b{i}", "question_id": "triage", "jev": {"choice": "billing", "confidence": 0.95}, "truth": "billing"})
        rows.append({"case_id": f"t{i}", "question_id": "triage", "answer": "tech", "confidence": 0.93, "truth": "tech"})
    for i in range(60):
        rows.append({"case_id": f"u{i}", "question_id": "triage", "jev": {"choice": "billing", "confidence": 0.55}, "truth": "human"})
    return rows


class JevGateTests(unittest.TestCase):
    def run_gate(self, rows, *commands):
        """Run each command against one log in one directory; return (exit codes, outputs, directory files)."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "d.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            codes, outputs = [], []
            for command in commands:
                argv = [a.replace("{dir}", directory) for a in command]
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    codes.append(jev_gate.main(argv))
                outputs.append(out.getvalue())
            files = {p.name: p.read_text() for p in path.iterdir() if p.suffix == ".json"}
        return codes, outputs, files

    def calibrate_then_evaluate(self, rows, *extra):
        return self.run_gate(
            rows,
            ["calibrate", "{dir}/d.jsonl", "--abstain", "human", "--out", "{dir}/t.json"],
            ["evaluate", "{dir}/d.jsonl", "--thresholds", "{dir}/t.json", "--json", "{dir}/e.json", *extra],
        )

    def test_jev_answer_shapes(self):
        self.assertEqual(jev_gate.jev_answer({"choice": "a", "confidence": 0.8}), ("a", 0.8))
        self.assertEqual(jev_gate.jev_answer({"noul": 0.8}), ("yes", 0.8))
        self.assertEqual(jev_gate.jev_answer({"noul": 0.1}), ("no", 0.9))
        self.assertEqual(jev_gate.jev_answer({"score": 2.4, "confidence": 0.7}), ("2", 0.7))

    def test_calibrate_then_evaluate_approves_clean_history(self):
        codes, outputs, files = self.calibrate_then_evaluate(decisions())
        policy = json.loads(files["t.json"])
        self.assertEqual(policy["abstain"], {"triage": "human"})
        self.assertEqual(policy["questions"]["triage"]["actions"], {"billing": 0.9, "tech": 0.9})
        result = json.loads(files["e.json"])
        self.assertEqual(codes, [0, 0], outputs[1])
        self.assertTrue(result["approved"])
        self.assertEqual(result["metrics"]["boundary_accuracy"]["pct"], 1.0)
        self.assertEqual(result["metrics"]["abstention_accuracy"]["pct"], 1.0)
        self.assertLess(result["acted"], result["evaluated"])  # the low-confidence cases went to a person
        self.assertIn("RELEASE GATE: APPROVE", outputs[1])

    def test_without_thresholds_everything_abstains_and_holds(self):
        codes, outputs, _ = self.run_gate(decisions(), ["evaluate", "{dir}/d.jsonl", "--abstain", "human"])
        self.assertEqual(codes, [2])
        self.assertIn("no calibrated threshold", outputs[0])

    def test_machine_labels_never_judge_the_gate(self):
        rows = [{**r, "label_source": "llm"} for r in decisions()]
        codes, outputs, _ = self.calibrate_then_evaluate(rows)
        self.assertEqual(codes[1], 2)
        self.assertIn("evaluated=0", outputs[1])
        self.assertIn("fewer than", outputs[1])

    def test_a_wrong_confident_answer_holds(self):
        rows = decisions()
        for i in range(0, 120, 4):
            rows[2 * i]["truth"] = "tech"  # every fourth confident billing answer is wrong
        codes, outputs, _ = self.calibrate_then_evaluate(rows)
        self.assertEqual(codes[1], 2)

    def test_the_abstain_answer_is_never_gated(self):
        policy = {"default_when_uncalibrated": "abstain"}
        record = jev_gate.normalize({"case_id": "c", "question_id": "q", "answer": "human", "confidence": 0.1})
        replayed = jev_gate.replay(record, policy, "human")
        self.assertEqual((replayed["action_id"], replayed["abstained"]), ("human", False))
        self.assertEqual(jev_gate.threshold_for(policy, "q", "billing", "human"), jev_gate.UNCALIBRATED)

    def test_abstain_overrides_apply_over_the_saved_policy(self):
        base = {"q1": "human", "q2": "review"}
        self.assertEqual(jev_gate.parse_abstain(["q2=escalate"], {"q1", "q2"}, base=base), {"q1": "human", "q2": "escalate"})
        self.assertEqual(jev_gate.parse_abstain([], {"q1", "q3"}, base=base), {"q1": "human", "q3": "abstain"})

    def test_illegal_answer_holds(self):
        rows = decisions() + [{"case_id": f"x{i}", "question_id": "triage", "answer": "tech", "confidence": 0.97,
                               "truth": "tech", "legal": ["billing"]} for i in range(20)]
        codes, outputs, _ = self.calibrate_then_evaluate(rows, "--all")
        self.assertEqual(codes[1], 2)
        self.assertIn("illegal", outputs[1])


if __name__ == "__main__":
    unittest.main()
