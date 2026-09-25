"""Cold-start hypothesis selection and privacy-safe workflow observation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forge  # noqa: E402
import observe_decision_events as observation  # noqa: E402


FIXTURE = ROOT / "examples" / "cold-start-agent"
ACTIONS = ["accept", "escalate", "revise", "search_more"]


def event(
    event_id: str,
    case_id: str,
    occurred_at: str,
    selected_action: str,
    *,
    state_before: dict | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "case_id": case_id,
        "opportunity_id": "choose_next_research_step",
        "occurred_at": occurred_at,
        "actor_type": "agent",
        "state_before": state_before or {"phase": "research"},
        "available_actions": ACTIONS,
        "selected_action": selected_action,
        "state_after": {"phase": selected_action},
        "source_ref": "research-agent:test",
    }


class EventObservationTests(unittest.TestCase):
    def scanned_project(self, root: Path) -> Path:
        project = root / "forge-project"
        forge.scan_project(
            project=project,
            sources=[FIXTURE],
            system_id="research_agent",
        )
        return project

    def write_events(self, path: Path, records: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
            encoding="utf-8",
        )

    def test_select_writes_an_instrumentation_contract_for_one_bounded_hypothesis(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.scanned_project(Path(directory))

            manifest = forge.select_opportunity(
                project,
                opportunity_id="choose_next_research_step",
            )

            self.assertEqual(manifest["stage"], "instrumentation_ready")
            self.assertEqual(manifest["next_action"], "collect_observations")
            self.assertEqual(manifest["selected_opportunity"], "choose_next_research_step")
            self.assertFalse(manifest["safety"]["shadow_eligible"])

            spec = yaml.safe_load((project / "instrumentation_spec.yaml").read_text())
            self.assertEqual(spec["opportunity"]["candidate_actions"], ACTIONS)
            self.assertIn("opportunity_id", spec["event_contract"]["required_fields"])
            self.assertFalse(spec["safety"]["captures_chain_of_thought"])

            schema = json.loads((project / "event_schema.json").read_text())
            self.assertEqual(schema["additionalProperties"], False)
            self.assertEqual(schema["properties"]["opportunity_id"]["const"], "choose_next_research_step")
            self.assertEqual(schema["properties"]["selected_action"]["enum"], ACTIONS)

    def test_non_semantic_opportunity_cannot_be_selected_for_jev_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.scanned_project(Path(directory))

            with self.assertRaisesRegex(forge.ProductStateError, "bounded semantic"):
                forge.select_opportunity(project, opportunity_id="max_retries")

    def test_observe_compiles_repeated_agent_paths_without_persisting_raw_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.scanned_project(root)
            forge.select_opportunity(project, opportunity_id="choose_next_research_step")
            events = root / "events.jsonl"
            self.write_events(
                events,
                [
                    event("e1", "case-secret-1", "2026-09-25T10:00:00Z", "search_more"),
                    event("e2", "case-secret-1", "2026-09-25T10:01:00Z", "accept"),
                    event("e3", "case-secret-2", "2026-09-25T11:00:00Z", "search_more"),
                    event("e4", "case-secret-2", "2026-09-25T11:01:00Z", "accept"),
                    event("e5", "case-secret-3", "2026-09-25T12:00:00Z", "revise"),
                    event("e6", "case-secret-3", "2026-09-25T12:01:00Z", "accept"),
                ],
            )

            manifest = forge.observe_project(project, events=events)

            self.assertEqual(manifest["stage"], "workflow_map_ready")
            self.assertEqual(manifest["next_action"], "review_decision_system_map")
            self.assertEqual(manifest["observations"]["events"], 6)
            self.assertEqual(manifest["observations"]["cases"], 3)
            self.assertEqual(manifest["observations"]["repeated_paths"], 1)
            self.assertFalse(manifest["safety"]["raw_event_state_persisted"])

            decision_map = yaml.safe_load((project / "decision_system_map.yaml").read_text())
            repeated = next(item for item in decision_map["observed_paths"] if item["case_count"] == 2)
            self.assertEqual(
                [step["selected_action"] for step in repeated["steps"]],
                ["search_more", "accept"],
            )
            self.assertTrue(decision_map["safety"]["descriptive_only"])
            rendered = (project / "decision_system_map.yaml").read_text()
            self.assertNotIn("case-secret", rendered)
            self.assertNotIn("phase:", rendered)

            observation_manifest = yaml.safe_load(
                (project / "evidence" / "observation_manifest.yaml").read_text()
            )
            self.assertEqual(observation_manifest["event_source"]["locator"], str(events.resolve()))
            self.assertEqual(len(observation_manifest["event_source"]["sha256"]), 64)

    def test_observe_keeps_collecting_when_no_path_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.scanned_project(root)
            forge.select_opportunity(project, opportunity_id="choose_next_research_step")
            events = root / "events.jsonl"
            self.write_events(
                events,
                [
                    event("e1", "case-1", "2026-09-25T10:00:00Z", "accept"),
                    event("e2", "case-2", "2026-09-25T11:00:00Z", "revise"),
                ],
            )

            manifest = forge.observe_project(project, events=events)

            self.assertEqual(manifest["stage"], "observations_collected")
            self.assertEqual(manifest["next_action"], "collect_more_observations")

    def test_raw_event_log_must_remain_outside_the_forge_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.scanned_project(root)
            forge.select_opportunity(project, opportunity_id="choose_next_research_step")
            events = project / "raw-events.jsonl"
            self.write_events(
                events,
                [event("e1", "case-1", "2026-09-25T10:00:00Z", "accept")],
            )

            with self.assertRaisesRegex(forge.ProductStateError, "outside the Forge project"):
                forge.observe_project(project, events=events)

    def test_event_contract_rejects_illegal_actions_hidden_reasoning_and_ambiguous_order(self):
        opportunity = {
            "opportunity_id": "choose_next_research_step",
            "actor_type": "agent",
            "candidate_actions": ACTIONS,
        }

        illegal = event("e1", "case-1", "2026-09-25T10:00:00Z", "delete")
        with self.assertRaisesRegex(observation.ObservationError, "selected_action"):
            observation.validate_events([illegal], opportunity)

        hidden = event(
            "e1",
            "case-1",
            "2026-09-25T10:00:00Z",
            "accept",
            state_before={"private_reasoning": "I guessed"},
        )
        with self.assertRaisesRegex(observation.ObservationError, "private reasoning"):
            observation.validate_events([hidden], opportunity)

        same_time = [
            event("e1", "case-1", "2026-09-25T10:00:00Z", "search_more"),
            event("e2", "case-1", "2026-09-25T10:00:00Z", "accept"),
        ]
        with self.assertRaisesRegex(observation.ObservationError, "ambiguous event order"):
            observation.validate_events(same_time, opportunity)

    def test_cli_runs_scan_select_and_observe_as_one_product_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "forge-project"
            events = root / "events.jsonl"
            self.write_events(
                events,
                [
                    event("e1", "case-1", "2026-09-25T10:00:00Z", "search_more"),
                    event("e2", "case-1", "2026-09-25T10:01:00Z", "accept"),
                    event("e3", "case-2", "2026-09-25T11:00:00Z", "search_more"),
                    event("e4", "case-2", "2026-09-25T11:01:00Z", "accept"),
                ],
            )
            commands = [
                [
                    sys.executable,
                    str(ROOT / "scripts" / "forge.py"),
                    "scan",
                    "--project",
                    str(project),
                    "--source",
                    str(FIXTURE),
                    "--system-id",
                    "research_agent",
                ],
                [
                    sys.executable,
                    str(ROOT / "scripts" / "forge.py"),
                    "select",
                    str(project),
                    "--opportunity",
                    "choose_next_research_step",
                ],
                [
                    sys.executable,
                    str(ROOT / "scripts" / "forge.py"),
                    "observe",
                    str(project),
                    "--events",
                    str(events),
                ],
            ]

            results = [
                subprocess.run(command, text=True, capture_output=True, check=False)
                for command in commands
            ]

            self.assertTrue(all(item.returncode == 0 for item in results), results)
            self.assertIn("HYPOTHESES READY", results[0].stdout)
            self.assertIn("INSTRUMENTATION READY", results[1].stdout)
            self.assertIn("WORKFLOW MAP READY", results[2].stdout)


if __name__ == "__main__":
    unittest.main()
