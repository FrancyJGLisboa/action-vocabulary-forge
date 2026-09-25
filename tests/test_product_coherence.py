from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import sys
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forge  # noqa: E402
import product_lifecycle  # noqa: E402


EXPECTED_STAGES = {
    "no_opportunities": ["add_operational_material"],
    "hypotheses_ready": ["select_candidate_for_instrumentation"],
    "instrumentation_ready": ["collect_observations"],
    "observations_collected": ["collect_more_observations"],
    "workflow_map_ready": ["review_decision_system_map"],
    "insufficient_evidence": ["add_evidence_and_rediscover"],
    "awaiting_review": ["review_candidate"],
    "ready_for_shadow": ["run_shadow"],
    "shadow_measured": ["verify_labels_and_calibrate", "calibrate_and_run_release_gate"],
    "evidence_inventoried": ["complete_system2_reconstruction"],
    "work_system_mapped": ["review_decision_opportunities"],
    "integration_planned": ["implement_and_verify_integration_contracts"],
    "binding_candidates_ready": ["run_candidates_in_controlled_host_and_emit_binding_observations"],
    "bindings_verified_for_shadow": ["implement_and_verify_state_legality_controller_and_bounded_loop"],
    "shadow_controller_ready": ["run_shadow_controller_and_collect_trusted_labels"],
}


class LifecycleContractTests(unittest.TestCase):
    def test_contract_declares_exact_real_stage_and_next_action_vocabulary(self):
        lifecycle = forge.load_lifecycle()
        self.assertEqual(product_lifecycle.validate_contract(lifecycle), [])
        actual = {
            stage: definition["manifest_next_actions"]
            for stage, definition in lifecycle["stages"].items()
        }
        self.assertEqual(actual, EXPECTED_STAGES)

    def test_every_transition_target_exists_and_every_entry_path_starts_at_a_real_stage(self):
        lifecycle = forge.load_lifecycle()
        stages = set(lifecycle["stages"])
        for definition in lifecycle["stages"].values():
            for transition in definition["transitions"]:
                self.assertIn(transition["to"], stages)
        for entry in lifecycle["entry_paths"].values():
            self.assertTrue(set(entry["initial_stages"]) <= stages)

    def test_validator_rejects_stage_drift_dangling_transitions_and_unsafe_contracts(self):
        lifecycle = forge.load_lifecycle()
        lifecycle["entry_paths"]["work_system"]["initial_stages"] = ["invented_stage"]
        lifecycle["stages"]["evidence_inventoried"]["transitions"][0]["to"] = "missing_stage"
        lifecycle["safety"].remove("zero_production_authority")
        errors = product_lifecycle.validate_contract(lifecycle)
        self.assertTrue(any("known stages" in error for error in errors))
        self.assertTrue(any("missing_stage" in error for error in errors))
        self.assertTrue(any("zero_production_authority" in error for error in errors))

    def test_project_manifest_must_match_canonical_stage_and_next_action(self):
        lifecycle = forge.load_lifecycle()
        valid = {
            "schema_version": "1.0",
            "system_id": "example",
            "stage": "evidence_inventoried",
            "next_action": "complete_system2_reconstruction",
        }
        self.assertEqual(product_lifecycle.validate_manifest(lifecycle, valid), [])
        drifted = dict(valid, next_action="whatever_survived_the_prompt")
        self.assertTrue(product_lifecycle.validate_manifest(lifecycle, drifted))

    def test_manifest_writer_refuses_a_transition_not_declared_by_the_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = {
                "schema_version": "1.0",
                "system_id": "example",
                "stage": "work_system_mapped",
                "next_action": "review_decision_opportunities",
                "safety": {"executes_actions": False, "production_authority": False},
            }
            with self.assertRaisesRegex(forge.ProductStateError, "forbids transition"):
                forge._write_project(
                    root,
                    document,
                    before_stage="evidence_inventoried",
                    via="shadow",
                )
            self.assertFalse((root / forge.PROJECT_FILE).exists())


class StartJourneyTests(unittest.TestCase):
    def _start_source(self, source: Path, system_id: str) -> tuple[Path, dict]:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        project = Path(self.directory.name) / "project"
        result = forge.start_project(
            project=project,
            system_id=system_id,
            repositories=[],
            urls=[],
            sources=[source],
        )
        return project, result

    def test_repository_code_journey_runs_real_acquisition_to_justified_stage(self):
        project, result = self._start_source(ROOT / "examples" / "cold-start-agent", "code_system")
        self.assertEqual(result["stage"], "evidence_inventoried")
        self.assertGreater(result["summary"]["sources"], 0)
        self.assertTrue((project / "evidence_manifest.yaml").is_file())
        self.assertFalse(result["safety"]["production_authority"])

    def test_documents_sop_journey_runs_real_acquisition_to_justified_stage(self):
        project, result = self._start_source(
            ROOT / "tests" / "fixtures" / "product-contract" / "documents-only",
            "document_system",
        )
        self.assertEqual(result["stage"], "evidence_inventoried")
        catalog = yaml.safe_load((project / "evidence" / "source_catalog.yaml").read_text(encoding="utf-8"))
        self.assertIn("policy_document", {item["kind"] for item in catalog["sources"]})

    def test_agentic_runtime_journey_runs_real_acquisition_to_justified_stage(self):
        project, result = self._start_source(
            ROOT / "tests" / "fixtures" / "product-contract" / "typescript-dot",
            "agentic_system",
        )
        self.assertEqual(result["stage"], "evidence_inventoried")
        catalog = yaml.safe_load((project / "evidence" / "source_catalog.yaml").read_text(encoding="utf-8"))
        self.assertTrue({"code", "document"} <= {item["kind"] for item in catalog["sources"]})

    def test_resolved_case_journey_runs_real_discovery_with_public_field_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            result = forge.start_project(
                project=project,
                system_id="shipment_triage",
                repositories=[],
                urls=[],
                sources=[ROOT / "examples" / "discovery-input" / "sop.md"],
                cases=ROOT / "examples" / "discovery-input" / "cases.jsonl",
                surface_field="decision_type",
            )
            self.assertEqual(result["stage"], "awaiting_review")
            self.assertEqual(result["selected_surface"], "shipment_exception")
            self.assertFalse(result["safety"]["production_authority"])

    def test_cases_never_silently_drop_repository_or_url_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(forge.ProductStateError, "would be ignored"):
                forge.start_project(
                    project=Path(directory) / "project",
                    system_id="unsafe",
                    repositories=["https://github.com/example/project"],
                    urls=["https://example.test/sop.md"],
                    sources=[],
                    cases=ROOT / "examples" / "discovery-input" / "cases.jsonl",
                )

    def test_start_cli_exposes_case_field_mapping(self):
        parsed = forge.parser().parse_args(
            [
                "start", "--project", "out", "--system-id", "system",
                "--cases", "cases.jsonl", "--surface-field", "decision_type",
                "--text-field", "body", "--action-field", "decision",
            ]
        )
        self.assertEqual(parsed.surface_field, "decision_type")
        self.assertEqual(parsed.text_field, "body")
        self.assertEqual(parsed.action_field, "decision")


class GuidanceTests(unittest.TestCase):
    def _project(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        material = root / "SOP.md"
        material.write_text("Operators choose accept or revise.\n", encoding="utf-8")
        project = root / "project"
        forge.start_project(
            project=project,
            system_id="guidance",
            repositories=[],
            urls=[],
            sources=[material],
        )
        return project

    def test_inspect_combines_persisted_project_and_canonical_guidance(self):
        project = self._project()
        inspection = forge.inspect_project(project)
        self.assertEqual(inspection["project"]["stage"], "evidence_inventoried")
        self.assertEqual(inspection["guidance"]["action_id"], "complete_system2_reconstruction")
        self.assertIn(str(project), inspection["guidance"]["command"])
        self.assertFalse(inspection["guidance"]["automatic"])
        self.assertFalse(inspection["authority"]["production"])

    def test_inspect_json_is_the_combined_contract_not_only_the_raw_manifest(self):
        project = self._project()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = forge.main(["inspect", str(project), "--json"])
        self.assertEqual(exit_code, 0)
        value = json.loads(output.getvalue())
        self.assertIn("project", value)
        self.assertIn("guidance", value)
        self.assertIn("authority", value)

    def test_continue_is_actionable_and_byte_for_byte_non_mutating(self):
        project = self._project()
        manifest_path = project / forge.PROJECT_FILE
        before = manifest_path.read_bytes()
        message = forge.render_next_action(project)
        after = manifest_path.read_bytes()
        self.assertEqual(before, after)
        self.assertIn("Required input", message)
        self.assertIn("work_system_proposal", message)
        self.assertIn("python3", message)
        self.assertIn("does not grant execution authority", message)

    def test_unknown_stage_or_next_action_is_rejected_instead_of_improvised(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            forge._write_yaml(
                project / forge.PROJECT_FILE,
                {
                    "schema_version": "1.0",
                    "system_id": "x",
                    "stage": "unknown",
                    "next_action": "guess",
                },
            )
            with self.assertRaisesRegex(forge.ProductStateError, "lifecycle"):
                forge.load_project(project)


class DocumentationTests(unittest.TestCase):
    def test_public_journey_is_canonical_and_advanced_surface_is_named(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        target = (ROOT / "docs" / "product-target.md").read_text(encoding="utf-8")
        product = (ROOT / "docs" / "product-v1.md").read_text(encoding="utf-8")
        for document in (readme, skill):
            self.assertIn("start", document)
            self.assertIn("inspect", document)
            self.assertIn("continue", document)
            self.assertIn("advanced", document.lower())
        self.assertIn("product_lifecycle.yaml", readme)
        self.assertIn("product_lifecycle.yaml", skill)
        self.assertIn("product_lifecycle.yaml", target)
        self.assertIn("start → inspect → continue", product)


if __name__ == "__main__":
    unittest.main()
