"""Semantic material -> judgments -> legal action -> runtime tests."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import import_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_adapter  # noqa: E402
import init_semantic_bundle  # noqa: E402
import semantic_runtime  # noqa: E402
import semantic_index  # noqa: E402
import validate_semantic_bundle  # noqa: E402


EXAMPLE = ROOT / "examples" / "semantic-validation-bundle"


class SemanticValidatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.bundle = Path(self.directory) / "bundle"
        shutil.copytree(EXAMPLE, self.bundle)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def edit(self, name, mutate):
        path = self.bundle / name
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(doc)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def test_example_is_valid_and_traces_the_complete_path(self):
        errors, warnings, counts = validate_semantic_bundle.validate(self.bundle)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(counts["sources"], 1)
        self.assertGreaterEqual(counts["concepts"], 1)
        self.assertGreaterEqual(counts["judgments"], 1)
        self.assertGreaterEqual(counts["linked_surfaces"], 1)

    def test_reviewed_judgment_requires_production_evidence(self):
        self.edit(
            "judgment_registry.yaml",
            lambda doc: doc["judgments"][0].__setitem__("evidence_refs", ["hypothesis_failure_tone"]),
        )
        errors, _, _ = validate_semantic_bundle.validate(self.bundle)
        self.assertTrue(any("reviewed judgment" in error and "production-grade" in error for error in errors), errors)

    def test_judgment_cannot_authorize_actions(self):
        self.edit(
            "judgment_registry.yaml",
            lambda doc: doc["judgments"][0].__setitem__("authorizes_actions", ["retry"]),
        )
        errors, _, _ = validate_semantic_bundle.validate(self.bundle)
        self.assertTrue(any("must not authorize actions" in error for error in errors), errors)

    def test_surface_supporting_judgment_must_exist(self):
        self.edit(
            "decision_surfaces.yaml",
            lambda doc: doc["decision_surfaces"][0].__setitem__("supporting_judgments", ["ghost"]),
        )
        errors, _, _ = validate_semantic_bundle.validate(self.bundle)
        self.assertTrue(any("unknown supporting judgment" in error for error in errors), errors)

    def test_every_semantic_document_requires_version_and_system_id(self):
        self.edit("semantic_ir.yaml", lambda doc: doc.pop("system_id"))
        self.edit("semantic_links.yaml", lambda doc: doc.__setitem__("schema_version", "1.0"))
        errors, _, _ = validate_semantic_bundle.validate(self.bundle)
        self.assertTrue(any("semantic_ir.yaml: system_id" in error for error in errors), errors)
        self.assertTrue(any("semantic_links.yaml: schema_version" in error for error in errors), errors)


class SemanticExampleLockstepTests(unittest.TestCase):
    def test_initializer_matches_checked_in_example(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "bundle"
            init_semantic_bundle.write_bundle(generated, init_semantic_bundle.example_bundle(), False)
            expected = sorted(path.relative_to(EXAMPLE) for path in EXAMPLE.iterdir() if path.is_file())
            actual = sorted(path.relative_to(generated) for path in generated.iterdir() if path.is_file())
            self.assertEqual(actual, expected)
            for relative in expected:
                self.assertEqual(
                    (generated / relative).read_bytes(),
                    (EXAMPLE / relative).read_bytes(),
                    str(relative),
                )


class SemanticRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.bundle = EXAMPLE
        self.adapter = import_bundle(generate_adapter.load_bundle(self.bundle))
        self.state = {
            "state_id": "validation_failed",
            "validation_status": "failed",
            "record_id": "r-1",
            "retry_budget": 1,
            "record_exists": True,
            "unresolved_failure": True,
        }

    def test_compile_plan_activates_judgments_and_filters_actions(self):
        plan = semantic_runtime.compile_plan(
            self.bundle,
            {"error": "upstream timed out"},
            self.state,
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(set(plan["judgment_request"]["questions"]), {"failure_is_transient"})
        criteria = plan["action_request"]["questions"]["next_validation_action"]["criteria"]
        self.assertEqual(set(criteria), {"retry", "human_review"})

        state = {**self.state, "retry_budget": 0}
        plan = semantic_runtime.compile_plan(self.bundle, {"error": "timeout"}, state)
        self.assertEqual(plan["status"], "deterministic")
        self.assertEqual(plan["action_id"], "human_review")

    def test_candidate_judgment_is_not_compiled(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(self.bundle, bundle)
            path = bundle / "judgment_registry.yaml"
            doc = yaml.safe_load(path.read_text())
            doc["judgments"][0]["maturity"] = "candidate"
            path.write_text(yaml.safe_dump(doc, sort_keys=False))
            plan = semantic_runtime.compile_plan(bundle, {}, self.state)
            self.assertEqual(plan["judgment_request"]["questions"], {})

    def test_two_stage_runtime_executes_only_a_legal_action(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            if "failure_is_transient" in payload["questions"]:
                return {
                    "answers": {"failure_is_transient": {"noul": 0.96}},
                    "model": "jev-test",
                    "usage": {"input_tokens": 12, "output_tokens": 1},
                }
            return {
                "answers": {
                    "next_validation_action": {
                        "choice": "retry",
                        "confidence": 0.96,
                        "probabilities": {"retry": 0.96, "human_review": 0.04},
                    }
                },
                "model": "jev-test",
                "usage": {"input_tokens": 15, "output_tokens": 2},
            }

        sys.modules["validation_client"] = types.SimpleNamespace(retry=lambda record_id: f"started:{record_id}")
        try:
            decision, result, judgments = semantic_runtime.run(
                self.adapter,
                self.bundle,
                {"error": "upstream timed out"},
                self.state,
                case_id="case-1",
                transport=transport,
            )
        finally:
            del sys.modules["validation_client"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(judgments["failure_is_transient"]["noul"], 0.96)
        self.assertEqual(decision.action_id, "retry")
        self.assertEqual(result, "started:r-1")
        self.assertIn("semantic_judgments", calls[1]["state"])

    def test_single_legal_action_is_resolved_without_action_request(self):
        state = {**self.state, "retry_budget": 0}
        # Only human_review is legal, so the runtime resolves deterministically and never asks JEV.
        calls = []

        def transport(payload):
            calls.append(payload)
            return {"answers": {"failure_is_transient": {"noul": 0.4}}, "model": "jev-test"}

        decision, _, _ = semantic_runtime.run(
            self.adapter,
            self.bundle,
            {},
            state,
            transport=transport,
            handlers={"human_review": lambda call_state: "queued"},
        )
        self.assertEqual(decision.action_id, "human_review")
        self.assertEqual(len(calls), 1)  # supporting judgments only

    def test_action_response_outside_exposed_legal_set_is_rejected(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            if len(calls) == 1:
                return {"answers": {"failure_is_transient": {"noul": 0.4}}, "model": "jev-test"}
            return {
                "answers": {"next_validation_action": {"choice": "delete_everything"}},
                "model": "jev-test",
            }

        with self.assertRaises(self.adapter.IllegalChoice):
            semantic_runtime.run(self.adapter, self.bundle, {}, self.state, transport=transport)
        self.assertEqual(len(calls), 2)


class SemanticLogTests(unittest.TestCase):
    def test_judgment_log_is_jev_gate_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "judgments.jsonl"
            log = semantic_runtime.JudgmentLog(path)
            log.append_response(
                case_id="c1",
                response={"answers": {"q": {"noul": 0.8}}, "model": "jev-test", "usage": {"input_tokens": 2}},
            )
            record = json.loads(path.read_text().strip())
            self.assertEqual(record["question_id"], "q")
            self.assertEqual(record["jev"]["noul"], 0.8)
            self.assertEqual(record["model"], "jev-test")


class SemanticIndexTests(unittest.TestCase):
    def test_bundle_is_searchable_across_judgments_and_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "semantic.sqlite"
            count = semantic_index.build(EXAMPLE, database)
            self.assertGreater(count, 10)
            results = semantic_index.search(database, "transient failure")
            kinds = {item["kind"] for item in results}
            self.assertIn("judgment", kinds)
            self.assertIn("action", kinds)
            # FTS operators and punctuation are treated as data, not query syntax.
            self.assertTrue(semantic_index.search(database, 'transient OR "failure"'))


if __name__ == "__main__":
    unittest.main()
