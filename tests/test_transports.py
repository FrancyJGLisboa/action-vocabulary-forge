import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import EXAMPLE, ROOT, choice_response, import_bundle, load_example  # noqa: E402

import evaluate_decisions  # noqa: E402
import transports  # noqa: E402

Q = "next_validation_action"


class FakeAgent:
    def __init__(self):
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((json.loads(json.dumps(state, default=str)), dict(questions)))
        (qid, q), = questions.items()
        first = next(iter(q["criteria"])) if isinstance(q.get("criteria"), dict) else None
        return {"answers": {qid: {"choice": first, "confidence": 0.91, "probabilities": {first: 0.91}}}}


def fake_laya(agent):
    module = types.SimpleNamespace(load=lambda repo, subfolder=None: agent)
    return mock.patch.dict(sys.modules, {"laya": module})


class LayaTransportTests(unittest.TestCase):
    def setUp(self):
        transports._LAYA_AGENTS.clear()

    def test_one_predict_per_question_and_compacted_context(self):
        agent = FakeAgent()
        with fake_laya(agent):
            send = transports.laya_transport("laya:typed-decisions", max_state_chars=50)
            response = send({"model": "x", "state": {"context": "y" * 500, "system_id": "s"},
                             "questions": {"a": {"type": "choice", "criteria": {"k1": "c1", "k2": "c2"}},
                                           "b": {"type": "choice", "criteria": {"k3": "c3"}}}})
        self.assertEqual(len(agent.calls), 2)
        self.assertEqual(len(agent.calls[0][0]["context"]), 50)
        self.assertEqual(agent.calls[0][0]["system_id"], "s")
        self.assertEqual(response["model"], "laya:typed-decisions")
        self.assertEqual(set(response["answers"]), {"a", "b"})
        self.assertEqual(response["answers"]["a"]["choice"], "k1")
        self.assertIn("input_chars", response["usage"])

    def test_missing_sdk_is_a_clear_error(self):
        with mock.patch.dict(sys.modules, {"laya": None}):
            with self.assertRaises(RuntimeError) as ctx:
                transports.laya_transport()({"questions": {"a": {"criteria": {"k": "c"}}}, "state": {}})
        self.assertIn("pip install laya", str(ctx.exception))

    def test_compact_mapping_keeps_leading_keys(self):
        out = transports._compact({"a": "x" * 10, "b": "y" * 100, "c": 1}, 40)
        self.assertIn("a", out)
        self.assertNotIn("c", out)


class ProviderSelectionTests(unittest.TestCase):
    def setUp(self):
        transports._LAYA_AGENTS.clear()

    def test_bundle_provider_laya_uses_local_transport(self):
        bundle = load_example()
        bundle["provider"] = "laya"
        bundle["model"] = "laya:typed-decisions"
        m = import_bundle(bundle)
        self.assertEqual(m.PROVIDER, "laya")
        agent = FakeAgent()
        with fake_laya(agent), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_PROVIDER", None)
            os.environ.pop("TYPESAFE_API_KEY", None)
            decision = m.classify({"error": "timeout"}, question_id=Q)
        self.assertEqual(decision.model, "laya:typed-decisions")
        self.assertEqual(decision.proposed_action_id, "retry")
        self.assertEqual(len(agent.calls), 1)

    def test_env_override_switches_provider(self):
        m = import_bundle(load_example())  # provider vendor_neutral -> http by default
        agent = FakeAgent()
        with fake_laya(agent), mock.patch.dict(os.environ, {"FORGE_PROVIDER": "laya"}, clear=False):
            decision = m.classify({"error": "timeout"}, question_id=Q)
        self.assertEqual(decision.model, "laya:typed-decisions")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_PROVIDER", None)
            os.environ.pop("TYPESAFE_API_KEY", None)
            with self.assertRaises(m.AdapterError):
                m.classify({"error": "timeout"}, question_id=Q)  # http path needs a key

    def test_missing_laya_becomes_adapter_error(self):
        bundle = load_example()
        bundle["provider"] = "laya"
        m = import_bundle(bundle)
        with mock.patch.dict(sys.modules, {"laya": None}):
            with self.assertRaises(m.AdapterError) as ctx:
                m.classify({"x": 1}, question_id=Q)
        self.assertIn("pip install laya", str(ctx.exception))

    def test_unknown_provider_rejected_by_generator_and_validator(self):
        import generate_adapter, validate_action_bundle
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(EXAMPLE, bundle)
            spec = bundle / "jev_adapter_spec.yaml"
            spec.write_text(spec.read_text().replace("provider: vendor_neutral", "provider: carrier_pigeon"))
            errors, _, _ = validate_action_bundle.validate(bundle)
            self.assertTrue(any("provider must be one of" in e for e in errors))
            with self.assertRaises(SystemExit):
                generate_adapter.load_bundle(bundle)


class ByModelTests(unittest.TestCase):
    def test_by_model_table_and_deterministic_line(self):
        def record(case, model, proposed, truth, conf, reason=None):
            return {"case_id": case, "question_id": Q, "surface_id": "resolve_validation_failure", "state_id": "validation_failed",
                    "proposed_action_id": proposed, "action_id": proposed, "confidence": conf, "abstained": False,
                    "legal_actions": ["retry", "human_review"], "outcome": "ok", "ground_truth_action_id": truth,
                    "model": model, "reason": reason, "latency_ms": 10.0}
        records = [record(f"j{i}", "jev-1", "retry", "retry", 0.95) for i in range(20)]
        records += [record(f"l{i}", "laya:typed-decisions", "retry", "retry" if i % 2 else "human_review", 0.9) for i in range(20)]
        records += [record(f"d{i}", None, "human_review", "human_review", None, reason="deterministic:single_legal_action") for i in range(5)]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "log.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in records))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                evaluate_decisions.main([str(EXAMPLE), "--log", str(log), "--all", "--by-model", "--min-boundary-accuracy", "0.5"])
        text = out.getvalue()
        self.assertIn("by model:", text)
        self.assertIn("jev-1", text)
        self.assertIn("laya:typed-decisions", text)
        self.assertIn("deterministic          5/5 (100.0%)", text)

    def test_deterministic_disagreement_holds(self):
        records = [{"case_id": f"d{i}", "question_id": Q, "proposed_action_id": "human_review", "action_id": "human_review",
                    "confidence": None, "abstained": True, "legal_actions": ["human_review"], "outcome": "ok",
                    "ground_truth_action_id": "retry" if i == 0 else "human_review", "reason": "deterministic:single_legal_action"}
                   for i in range(35)]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "log.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in records))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = evaluate_decisions.main([str(EXAMPLE), "--log", str(log), "--all"])
        self.assertEqual(code, 2)
        self.assertIn("host rule bug", out.getvalue())


if __name__ == "__main__":
    unittest.main()
