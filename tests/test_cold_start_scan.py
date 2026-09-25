"""Cold-start discovery without resolved cases or named decision surfaces."""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forge  # noqa: E402
import scan_decision_opportunities as scanner  # noqa: E402


FIXTURE = ROOT / "examples" / "cold-start-agent"


class ColdStartScanTests(unittest.TestCase):
    def test_scan_builds_an_opportunity_map_without_historical_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "research-agent"
            manifest = forge.scan_project(
                project=project,
                sources=[FIXTURE],
                system_id="research_agent",
            )

            self.assertEqual(manifest["stage"], "hypotheses_ready")
            self.assertEqual(manifest["next_action"], "select_candidate_for_instrumentation")
            self.assertEqual(manifest["summary"]["observed_cases"], 0)
            self.assertIsNone(manifest["artifacts"]["candidate_bundle"])
            self.assertFalse(manifest["safety"]["shadow_eligible"])

            opportunity_map = yaml.safe_load(
                (project / "decision_opportunity_map.yaml").read_text(encoding="utf-8")
            )
            by_id = {item["opportunity_id"]: item for item in opportunity_map["opportunities"]}
            self.assertEqual(
                {item["type"] for item in by_id.values()},
                {"deterministic_rule", "bounded_semantic_decision", "open_generation"},
            )
            self.assertEqual(by_id["max_retries"]["recommended_runtime"], "code")
            bounded = by_id["choose_next_research_step"]
            self.assertEqual(
                bounded["candidate_actions"],
                ["accept", "escalate", "revise", "search_more"],
            )
            self.assertEqual(bounded["recommended_runtime"], "observe_then_jev")
            self.assertEqual(by_id["draft_final_answer"]["recommended_runtime"], "llm")
            self.assertTrue(all(item["maturity"] == "hypothesis" for item in by_id.values()))
            self.assertTrue(all(item["observed_cases"] == 0 for item in by_id.values()))
            self.assertTrue(all("binding" not in item for item in by_id.values()))

            plan = yaml.safe_load((project / "observation_plan.yaml").read_text(encoding="utf-8"))
            self.assertEqual([item["opportunity_id"] for item in plan["plans"]], ["choose_next_research_step"])
            required = set(plan["event_contract"]["required_fields"])
            self.assertTrue(
                {"event_id", "case_id", "actor_type", "state_before", "available_actions", "selected_action"}
                <= required
            )
            self.assertIn(
                "safe no-match or fallback",
                " ".join(plan["plans"][0]["limitations"]),
            )

            review = (project / "scan_review.md").read_text(encoding="utf-8")
            self.assertIn("Resolved cases: 0", review)
            self.assertIn("not eligible for shadow evaluation", review)
            self.assertNotIn("Draft the final answer from this evidence", review)
            self.assertFalse((project / "candidate_bundle").exists())

    def test_system_two_proposal_cannot_invent_an_unsupported_action(self):
        sources = scanner.inventory_sources([FIXTURE])
        proposal = {
            "opportunity_id": "choose_destructive_step",
            "type": "bounded_semantic_decision",
            "candidate_actions": ["delete_project", "escalate"],
            "evidence": [
                {
                    "source_id": next(item["source_id"] for item in sources if item["locator"].endswith("tools.json")),
                    "line_start": 1,
                    "line_end": 8,
                }
            ],
        }

        with self.assertRaisesRegex(scanner.ScanError, "unsupported action.*delete_project"):
            scanner.validate_proposals([proposal], sources)

    def test_system_two_can_externalize_a_documented_human_decision_as_a_hypothesis(self):
        sources = scanner.inventory_sources([FIXTURE])
        sop = next(item for item in sources if item["locator"].endswith("SOP.md"))
        proposal = {
            "opportunity_id": "review_research_state",
            "type": "bounded_semantic_decision",
            "actor_type": "human",
            "candidate_actions": ["search_more", "revise", "accept", "escalate"],
            "evidence": [
                {
                    "source_id": sop["source_id"],
                    "line_start": 3,
                    "line_end": 5,
                }
            ],
        }

        normalized = scanner.validate_proposals([proposal], sources)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["actor_type"], "human")
        self.assertEqual(normalized[0]["maturity"], "hypothesis")
        self.assertFalse(normalized[0]["shadow_eligible"])
        self.assertEqual(
            normalized[0]["candidate_actions"],
            ["accept", "escalate", "revise", "search_more"],
        )

    def test_system_two_proposal_requires_a_real_identifier_and_string_actions(self):
        sources = scanner.inventory_sources([FIXTURE])
        sop = next(item for item in sources if item["locator"].endswith("SOP.md"))
        evidence = [{"source_id": sop["source_id"], "line_start": 3, "line_end": 5}]

        with self.assertRaisesRegex(scanner.ScanError, "opportunity_id is required"):
            scanner.validate_proposals(
                [{"opportunity_id": " ", "type": "open_generation", "evidence": evidence}],
                sources,
            )

        with self.assertRaisesRegex(scanner.ScanError, "each opportunity must be a mapping"):
            scanner.validate_proposals(["not a mapping"], sources)  # type: ignore[list-item]

        with self.assertRaisesRegex(scanner.ScanError, "unsupported actor_type"):
            scanner.validate_proposals(
                [
                    {
                        "opportunity_id": "bad_actor",
                        "type": "open_generation",
                        "actor_type": "oracle",
                        "evidence": evidence,
                    }
                ],
                sources,
            )

        with self.assertRaisesRegex(scanner.ScanError, "candidate_actions must be a list of strings"):
            scanner.validate_proposals(
                [
                    {
                        "opportunity_id": "bad_actions",
                        "type": "bounded_semantic_decision",
                        "candidate_actions": "accept, escalate",
                        "evidence": evidence,
                    }
                ],
                sources,
            )

    def test_system_two_accepts_natural_action_labels_and_rejects_changed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            material = Path(directory) / "workflow.md"
            material.write_text("The reviewer may search more or accept.\n", encoding="utf-8")
            sources = scanner.inventory_sources([material])
            proposal = {
                "opportunity_id": "review_next_step",
                "type": "bounded_semantic_decision",
                "actor_type": "human",
                "candidate_actions": ["search more", "accept"],
                "evidence": [
                    {
                        "source_id": sources[0]["source_id"],
                        "line_start": 1,
                        "line_end": 1,
                    }
                ],
            }

            normalized = scanner.validate_proposals([proposal], sources)
            self.assertEqual(normalized[0]["candidate_actions"], ["accept", "search_more"])

            material.write_text("The reviewer may wait.\n", encoding="utf-8")
            with self.assertRaisesRegex(scanner.ScanError, "changed after inventory"):
                scanner.validate_proposals([proposal], sources)

    def test_scan_with_no_decision_signals_returns_a_safe_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("A descriptive note with no actions or workflow.", encoding="utf-8")
            project = root / "empty-project"

            manifest = forge.scan_project(
                project=project,
                sources=[material],
                system_id="empty_setting",
            )

            self.assertEqual(manifest["stage"], "no_opportunities")
            self.assertEqual(manifest["next_action"], "add_operational_material")
            self.assertEqual(manifest["summary"]["opportunities"], 0)
            self.assertFalse(manifest["safety"]["shadow_eligible"])

    def test_generated_forge_projects_are_not_scanned_as_operating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "workflow.py").write_text("MAX_ATTEMPTS = 2\n", encoding="utf-8")
            generated = root / ".forge" / "system"
            generated.mkdir(parents=True)
            (generated / "proposal.py").write_text(
                'INVENTED_ACTIONS = ("approve", "delete")\n',
                encoding="utf-8",
            )

            sources = scanner.inventory_sources([root])

            self.assertEqual([Path(item["locator"]).name for item in sources], ["workflow.py"])

    def test_typescript_and_dot_discover_bounded_agent_decisions_with_line_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "review.ts").write_text(
                'const REVIEW_ACTIONS = ["lgtm", "revise", "escalate"];\n'
                'const outcome = choose(state, REVIEW_ACTIONS);\n', encoding="utf-8"
            )
            (root / "workflow.dot").write_text(
                'review -> done [on_label="lgtm"];\n'
                'review -> plan [on_label="revise"];\n'
                'review -> human [on_label="escalate"];\n', encoding="utf-8"
            )
            result = scanner.scan([root], system_id="fixture")
            by_id = {item["opportunity_id"]: item for item in result["opportunities"]}
            self.assertEqual(by_id["review_choose"]["candidate_actions"], ["escalate", "lgtm", "revise"])
            self.assertEqual(by_id["workflow_review"]["candidate_actions"], ["escalate", "lgtm", "revise"])
            self.assertTrue(all(item["maturity"] == "hypothesis" for item in by_id.values()))
            self.assertTrue(all(item["shadow_eligible"] is False for item in by_id.values()))
            self.assertTrue(all("binding" not in item for item in by_id.values()))
            ts_source = next(item for item in result["source_inventory"] if item["locator"].endswith("review.ts"))
            self.assertEqual(ts_source["kind"], "code")
            self.assertEqual(by_id["review_choose"]["evidence"][0]["line_start"], 2)

    def test_typescript_requires_named_bounded_vocabulary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "workflow.ts"
            source.write_text('const VALUES = ["a", "b"];\nchoose(state, VALUES);\n', encoding="utf-8")
            self.assertEqual(scanner.scan([source], system_id="fixture")["opportunities"], [])

    def test_select_emits_reviewable_capture_points_without_source_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "review.ts"
            source.write_text(
                'const REVIEW_ACTIONS = ["lgtm", "revise", "escalate"];\n'
                'const outcome = choose(state, REVIEW_ACTIONS);\n', encoding="utf-8"
            )
            project = root / "forge-project"
            forge.scan_project(project=project, sources=[source], system_id="fixture")
            before = source.read_bytes()
            manifest = forge.select_opportunity(project, opportunity_id="review_choose")
            spec = yaml.safe_load((project / "instrumentation_spec.yaml").read_text(encoding="utf-8"))
            points = spec["integration"]["suggested_capture_points"]
            self.assertEqual(manifest["stage"], "instrumentation_ready")
            self.assertEqual(len(points), 1)
            self.assertEqual(points[0]["line_start"], 2)
            self.assertTrue(points[0]["review_required"])
            self.assertEqual(spec["integration"]["scaffold_status"], "review_required_not_generated")
            self.assertTrue(spec["safety"]["observation_only"])
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
