"""Product-level vertical slice: discover -> review -> shadow measurement."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forge  # noqa: E402


FIXTURE = ROOT / "examples" / "discovery-input"


def transport_for_fixture(payload):
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


class ForgeProductTests(unittest.TestCase):
    def discover(self, project: Path) -> dict:
        return forge.discover_project(
            project=project,
            cases=FIXTURE / "cases.jsonl",
            sources=[FIXTURE / "sop.md"],
            system_id="shipment_triage",
            surface_field="decision_type",
            review_minutes=2.0,
            hourly_cost=45.0,
            monthly_volume=3000,
        )

    def test_discover_creates_a_product_project_and_human_review(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "shipment-forge"
            result = self.discover(project)

            self.assertEqual(result["stage"], "awaiting_review")
            self.assertEqual(result["selected_surface"], "shipment_exception")
            self.assertTrue((project / "forge_project.yaml").is_file())
            self.assertTrue((project / "review.md").is_file())
            self.assertTrue((project / "discovery" / "candidate_bundle").is_dir())

            manifest = yaml.safe_load((project / "forge_project.yaml").read_text())
            self.assertEqual(manifest["stage"], "awaiting_review")
            self.assertEqual(manifest["next_action"], "review_candidate")
            self.assertEqual(manifest["artifacts"]["review"], "review.md")
            self.assertTrue(manifest["created_at"].endswith("Z"))
            self.assertEqual(manifest["created_at"], manifest["updated_at"])

            review = (project / "review.md").read_text()
            self.assertIn("# Forge V1 review", review)
            self.assertIn("shipment_exception", review)
            self.assertIn("request_documents", review)
            self.assertIn("Shadow mode executes no actions", review)
            self.assertNotIn("Commercial invoice is missing", review)
            self.assertNotIn("ship-001", review)

    def test_insufficient_evidence_is_a_product_state_not_a_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = root / "cases.jsonl"
            cases.write_text(
                "\n".join(
                    [
                        json.dumps({"case_id": "one", "message": "first", "resolved_action": "approve"}),
                        json.dumps({"case_id": "two", "message": "second", "resolved_action": "reject"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            project = root / "weak-project"
            result = forge.discover_project(
                project=project,
                cases=cases,
                sources=[],
                system_id="weak_history",
            )

            self.assertEqual(result["stage"], "insufficient_evidence")
            self.assertIsNone(result["selected_surface"])
            review = (project / "review.md").read_text()
            self.assertIn("No candidate passed the evidence gate", review)
            self.assertIn("needs at least 20 cases", review)

    def test_shadow_is_refused_until_a_named_human_approves_the_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "shipment-forge"
            self.discover(project)

            with self.assertRaisesRegex(forge.ProductStateError, "approved for shadow"):
                forge.run_shadow(project, transport=transport_for_fixture)

            with self.assertRaisesRegex(forge.ProductStateError, "reviewer"):
                forge.approve_for_shadow(
                    project,
                    surface_id="shipment_exception",
                    reviewer="   ",
                )

    def test_review_and_shadow_complete_the_v1_vertical_slice(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "shipment-forge"
            self.discover(project)

            reviewed = forge.approve_for_shadow(
                project,
                surface_id="shipment_exception",
                reviewer="Domain Owner",
                notes="Vocabulary and historical labels are suitable for non-executing evaluation.",
            )
            self.assertEqual(reviewed["stage"], "ready_for_shadow")
            review_record = yaml.safe_load((project / "shadow_review.yaml").read_text())
            self.assertTrue(review_record["approval"]["shadow_only"])
            self.assertFalse(review_record["approval"]["production_authority"])
            self.assertTrue(review_record["reviewed_at"].endswith("Z"))

            measured = forge.run_shadow(
                project,
                transport=transport_for_fixture,
                label_source="historical",
            )
            self.assertEqual(measured["stage"], "shadow_measured")
            self.assertEqual(measured["shadow"]["status"], "measured_jev_holdout")
            self.assertEqual(measured["shadow"]["raw_accuracy"], 1.0)
            self.assertFalse(measured["shadow"]["release_gate_eligible"])
            self.assertEqual(measured["next_action"], "verify_labels_and_calibrate")

            persisted = forge.load_project(project)
            self.assertEqual(persisted["stage"], "shadow_measured")
            status = forge.render_status(project)
            self.assertIn("SHADOW MEASURED", status)
            self.assertIn("Raw accuracy: 100.0%", status)
            self.assertIn("not production approval", status)

    def test_review_cannot_approve_a_surface_that_was_not_compiled(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "shipment-forge"
            self.discover(project)

            with self.assertRaisesRegex(forge.ProductStateError, "compiled candidate"):
                forge.approve_for_shadow(
                    project,
                    surface_id="invented_surface",
                    reviewer="Domain Owner",
                )


if __name__ == "__main__":
    unittest.main()
