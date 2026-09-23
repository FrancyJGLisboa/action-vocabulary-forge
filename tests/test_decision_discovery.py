"""Resolved material -> ranked decision candidate -> safe bundle tests."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import discover_decision_system as discovery  # noqa: E402
import evaluate_discovered_system as discovered_eval  # noqa: E402
import validate_semantic_bundle  # noqa: E402


FIXTURE = ROOT / "examples" / "discovery-input"


def arguments(output: Path, cases: Path = FIXTURE / "cases.jsonl", **overrides) -> argparse.Namespace:
    values = {
        "cases": cases,
        "source": [FIXTURE / "sop.md"],
        "output": output,
        "system_id": "shipment_triage",
        "scope": "Choose a safe next step for a shipment exception.",
        "case_id_field": "case_id",
        "text_field": "message",
        "action_field": "resolved_action",
        "outcome_field": "outcome",
        "surface_field": "decision_type",
        "min_cases": 20,
        "min_action_cases": 3,
        "max_actions": 12,
        "test_fraction": 0.2,
        "confidence_threshold": 0.8,
        "review_minutes": 2.0,
        "hourly_cost": 45.0,
        "monthly_volume": 3000,
        "force": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class DiscoveryVerticalSliceTests(unittest.TestCase):
    def test_taxonomy_description_enriches_criteria_and_is_documented(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            taxonomy = root / "taxonomy.json"
            taxonomy.write_text(json.dumps({"labels": {"request_documents": "Ask for missing paperwork only."}}))
            output = root / "discovery"
            discovery.discover(arguments(output, source=[FIXTURE / "sop.md", taxonomy], taxonomy=taxonomy))
            bundle = output / "candidate_bundle"
            surfaces = yaml.safe_load((bundle / "decision_surfaces.yaml").read_text())
            row = next(item for item in surfaces["decision_surfaces"][0]["candidate_actions"] if item["action_id"] == "request_documents")
            self.assertIn("Ask for missing paperwork only", row["criterion"])
            evidence = (bundle / "evidence_ledger.jsonl").read_text()
            self.assertIn("taxonomy_request_documents", evidence)

    def test_ambiguous_or_absent_taxonomy_keeps_generic_criteria(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            taxonomy = root / "taxonomy.json"
            taxonomy.write_text(json.dumps({"labels": {"request_documents": "one", "expedite_shipment": "one"}}))
            output = root / "discovery"
            discovery.discover(arguments(output, source=[FIXTURE / "sop.md", taxonomy], taxonomy=taxonomy))
            bundle = output / "candidate_bundle"
            surfaces = yaml.safe_load((bundle / "decision_surfaces.yaml").read_text())
            row = next(item for item in surfaces["decision_surfaces"][0]["candidate_actions"] if item["action_id"] == "request_documents")
            self.assertIn("Case evidence supports 'request_documents'", row["criterion"])

    def test_discovers_ranks_measures_and_scaffolds_safe_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discovery"
            result = discovery.discover(arguments(output))
            self.assertEqual(result["cases"], 30)
            self.assertEqual(result["eligible"], 1)
            self.assertTrue((output / "discovery_report.md").is_file())
            self.assertTrue((output / "evaluation_cases.jsonl").is_file())

            candidates = yaml.safe_load((output / "decision_candidates.yaml").read_text())
            candidate = candidates["candidates"][0]
            self.assertEqual(candidate["surface_id"], "shipment_exception")
            self.assertEqual(candidate["status"], "candidate_for_human_review")
            self.assertEqual(candidate["action_count"], 3)
            self.assertEqual(candidate["baseline"]["status"], "measured_holdout")
            self.assertTrue(candidate["baseline"]["not_jev_performance"])
            self.assertGreaterEqual(candidate["baseline"]["accuracy"], 0.8)

            bundle = output / "candidate_bundle"
            errors, warnings, _ = validate_semantic_bundle.validate(bundle)
            self.assertEqual(errors, [])
            self.assertTrue(any("no binding" in warning for warning in warnings))
            surface = yaml.safe_load((bundle / "decision_surfaces.yaml").read_text())["decision_surfaces"][0]
            self.assertFalse(surface["production"])
            actions = yaml.safe_load((bundle / "action_registry.yaml").read_text())["actions"]
            for action in actions:
                self.assertNotIn("binding", action)
                if action["action_id"] != "human_review":
                    self.assertIn("human_approved == true", action["preconditions"])
                    self.assertTrue(action["requires_confirmation"])

            # A one-field production flip must fail: historical labels are not
            # production-grade evidence for an action contract.
            surface_path = bundle / "decision_surfaces.yaml"
            surfaces = yaml.safe_load(surface_path.read_text())
            surfaces["decision_surfaces"][0]["production"] = True
            surface_path.write_text(yaml.safe_dump(surfaces, sort_keys=False))
            promoted_errors, _, _ = validate_semantic_bundle.validate(bundle)
            self.assertTrue(any("lacks production-grade action evidence" in item for item in promoted_errors))

    def test_outputs_do_not_copy_raw_case_context_or_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discovery"
            discovery.discover(arguments(output))
            rendered = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("Commercial invoice is missing", rendered)
            self.assertNotIn("ship-001", rendered)
            evaluation = [json.loads(line) for line in (output / "evaluation_cases.jsonl").read_text().splitlines()]
            self.assertEqual(len(evaluation), 30)
            self.assertTrue(all(len(item["case_ref"]) == 16 for item in evaluation))

    def test_weak_history_is_ranked_but_does_not_generate_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "cases.jsonl"
            cases.write_text(
                '\n'.join(
                    [
                        json.dumps({"case_id": "one", "message": "first", "resolved_action": "approve"}),
                        json.dumps({"case_id": "two", "message": "second", "resolved_action": "reject"}),
                    ]
                )
                + "\n"
            )
            output = root / "output"
            result = discovery.discover(
                arguments(output, cases, source=[], surface_field=None, min_cases=20)
            )
            self.assertEqual(result["eligible"], 0)
            self.assertIsNone(result["candidate_bundle"])
            self.assertFalse((output / "candidate_bundle").exists())
            candidate = yaml.safe_load((output / "decision_candidates.yaml").read_text())["candidates"][0]
            self.assertEqual(candidate["status"], "insufficient_evidence")
            self.assertTrue(candidate["blocking_gaps"])

    def test_duplicate_case_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "cases.jsonl"
            cases.write_text(
                json.dumps({"case_id": "same", "message": "one", "resolved_action": "approve"})
                + "\n"
                + json.dumps({"case_id": "same", "message": "two", "resolved_action": "reject"})
                + "\n"
            )
            with self.assertRaisesRegex(discovery.DiscoveryError, "duplicate case ID"):
                discovery.discover(arguments(root / "output", cases, source=[], surface_field=None))

    def test_csv_cases_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = Path(directory) / "cases.csv"
            cases.write_text(
                "case_id,message,resolved_action,outcome\n"
                "c1,missing invoice,request_documents,resolved\n"
                "c2,urgent delay,expedite_shipment,resolved\n",
                encoding="utf-8",
            )
            loaded = discovery.load_cases(
                cases,
                case_id_field="case_id",
                text_field="message",
                action_field="resolved_action",
                outcome_field="outcome",
                surface_field=None,
            )
            self.assertEqual(len(loaded), 2)
            self.assertEqual({item.surface for item in loaded}, {"default_surface"})

    def test_stratified_holdout_is_disjoint_and_covers_every_action(self):
        cases = discovery.load_cases(
            FIXTURE / "cases.jsonl",
            case_id_field="case_id",
            text_field="message",
            action_field="resolved_action",
            outcome_field="outcome",
            surface_field="decision_type",
        )
        train, holdout = discovery.stratified_split(cases, 0.2)
        self.assertFalse({item.case_id for item in train} & {item.case_id for item in holdout})
        self.assertEqual({item.action_id for item in train}, {item.action_id for item in holdout})


class DiscoveredEvaluationTests(unittest.TestCase):
    def test_candidate_runs_in_shadow_on_original_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discovery"
            discovery.discover(arguments(output))

            def transport(payload):
                text = payload["state"]["context"].lower()
                if any(word in text for word in ("missing", "invoice", "bill", "packing", "paperwork", "document")):
                    choice = "request_documents"
                elif any(word in text for word in ("customs", "compliance", "sanctions", "inspection")):
                    choice = "escalate_compliance"
                elif any(word in text for word in ("delay", "urgent", "deadline", "expedite", "priority", "air freight")):
                    choice = "expedite_shipment"
                else:
                    choice = "human_review"
                question_id = next(iter(payload["questions"]))
                return {
                    "answers": {
                        question_id: {
                            "choice": choice,
                            "confidence": 0.95,
                            "probabilities": {choice: 0.95},
                        }
                    },
                    "model": "jev-test",
                }

            result = discovered_eval.evaluate(output, transport=transport)
            self.assertEqual(result["status"], "measured_jev_holdout")
            self.assertEqual(result["holdout_cases"], 6)
            self.assertEqual(result["raw_accuracy"], 1.0)
            self.assertFalse(result["release_gate_eligible"])
            log = Path(result["log"]).read_text()
            self.assertNotIn("ship-", log)
            self.assertNotIn("shipment is delayed", log)

    def test_illegal_shadow_answer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "discovery"
            discovery.discover(arguments(output))

            def transport(payload):
                question_id = next(iter(payload["questions"]))
                return {"answers": {question_id: {"choice": "invented_action", "confidence": 1.0}}}

            with self.assertRaisesRegex(discovered_eval.EvaluationError, "illegal choice"):
                discovered_eval.evaluate(output, transport=transport)

    def test_changed_case_source_invalidates_the_frozen_holdout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "cases.jsonl"
            cases.write_text((FIXTURE / "cases.jsonl").read_text(), encoding="utf-8")
            output = root / "discovery"
            discovery.discover(arguments(output, cases))
            cases.write_text(cases.read_text() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(discovered_eval.EvaluationError, "changed after discovery"):
                discovered_eval.evaluate(output, transport=lambda payload: {})


if __name__ == "__main__":
    unittest.main()
