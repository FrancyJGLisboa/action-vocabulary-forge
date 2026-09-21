"""init -> validate -> generate -> run with a fake JEV -> log -> calibrate -> evaluate."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import ROOT, choice_response, import_bundle  # noqa: E402

import calibrate_thresholds  # noqa: E402
import evaluate_decisions  # noqa: E402
import generate_adapter  # noqa: E402
import init_action_bundle  # noqa: E402
import validate_action_bundle  # noqa: E402

Q = "next_validation_action"


def scenario(i: int) -> tuple[str, str, float]:
    """(jev_choice, ground_truth, confidence) for case i."""
    slot = i % 10
    if slot < 7:
        return "retry", "retry", 0.95
    if slot == 7:
        return "human_review", "human_review", 0.9
    if slot == 8:
        return "retry", "human_review", 0.6   # wrong and unsure: policy must abstain
    return "retry", "retry", 0.7               # right but unsure: abstains under the initial 0.85 policy


def write_fixture(directory: str | Path, cases: int = 120) -> dict[str, Path]:
    """Write bundle/, decision_log.jsonl, labels.jsonl and blocked_log.jsonl under directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    bundle_dir = directory / "bundle"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    init_action_bundle.write_bundle(bundle_dir, init_action_bundle.example_bundle(), force=False)
    errors, warnings, _ = validate_action_bundle.validate(bundle_dir)
    assert not errors, errors

    module = import_bundle(generate_adapter.load_bundle(bundle_dir))
    module.HANDLERS["human_review"] = lambda state: f"ticket:{state['record_id']}"
    sys.modules["validation_client"] = types.SimpleNamespace(retry=lambda record_id: f"started:{record_id}")
    log_path = directory / "decision_log.jsonl"
    labels_path = directory / "labels.jsonl"
    blocked_path = directory / "blocked_log.jsonl"
    for path in (log_path, labels_path, blocked_path):
        path.unlink(missing_ok=True)
    log = module.DecisionLog(log_path)
    labels = []
    try:
        for i in range(cases):
            choice, truth, confidence = scenario(i)
            state = {"state_id": "validation_failed", "record_id": f"r-{i}", "retry_budget": 1, "record_exists": True, "unresolved_failure": True}
            module.run({"error": "timeout", "case": i}, state, case_id=f"c{i}", log=log, transport=lambda payload, c=choice, k=confidence: choice_response(Q, c, k))
            labels.append({"case_id": f"c{i}", "question_id": Q, "ground_truth_action_id": truth})
        # One blocked case, kept out of the main log so the gate sees it only when asked.
        blocked_log = module.DecisionLog(blocked_path)
        state = {"state_id": "validation_failed", "record_id": "r-blocked", "retry_budget": 0, "record_exists": True}
        with contextlib.suppress(module.ExecutionBlocked):
            module.run({"error": "timeout"}, state, case_id="blocked", log=blocked_log, transport=lambda payload: choice_response(Q, "retry", 0.99))
    finally:
        del sys.modules["validation_client"]
    labels_path.write_text("".join(json.dumps(item) + "\n" for item in labels), encoding="utf-8")
    return {"bundle": bundle_dir, "log": log_path, "labels": labels_path, "blocked_log": blocked_path}


class EndToEndTests(unittest.TestCase):
    def test_full_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_fixture(directory)
            records = [json.loads(line) for line in paths["log"].read_text().splitlines()]
            self.assertEqual(len(records), 120)
            first = records[0]
            self.assertEqual((first["executed"], first["outcome"], first["result_summary"]), (True, "ok", "'started:r-0'"))
            self.assertEqual(first["handler_status"], "generated:python_callable")
            unsure = [r for r in records if r["confidence"] < 0.85]
            self.assertTrue(unsure and all(r["abstained"] and r["action_id"] == "human_review" for r in unsure))
            blocked = json.loads(paths["blocked_log"].read_text().splitlines()[0])
            self.assertEqual(blocked["outcome"], "blocked")
            self.assertIn("preconditions failed", blocked["blocked_reason"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = calibrate_thresholds.main([str(paths["bundle"]), "--log", str(paths["log"]), "--labels", str(paths["labels"]), "--write"])
            self.assertEqual(code, 0)
            spec = yaml.safe_load((paths["bundle"] / "jev_adapter_spec.yaml").read_text())
            self.assertEqual(spec["policy"]["questions"][Q]["actions"]["retry"], 0.7)
            self.assertIn("calibrated_at", spec["policy"])

            eval_json = Path(directory) / "eval.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = evaluate_decisions.main([str(paths["bundle"]), "--log", str(paths["log"]), "--labels", str(paths["labels"]), "--json", str(eval_json)])
            result = json.loads(eval_json.read_text())
            self.assertTrue(result["approved"], result["failures"])
            self.assertEqual(code, 0)
            self.assertEqual(result["metrics"]["illegal_action_rate"]["count"], 0)
            self.assertGreaterEqual(result["evaluated"], 30)

            # The blocked case is an illegal offer: including it must HOLD.
            merged = Path(directory) / "merged.jsonl"
            merged.write_text(paths["log"].read_text() + paths["blocked_log"].read_text())
            labels = paths["labels"].read_text() + json.dumps({"case_id": "blocked", "question_id": Q, "ground_truth_action_id": "human_review"}) + "\n"
            paths["labels"].write_text(labels)
            with contextlib.redirect_stdout(io.StringIO()):
                code = evaluate_decisions.main([str(paths["bundle"]), "--log", str(merged), "--labels", str(paths["labels"]), "--all"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
