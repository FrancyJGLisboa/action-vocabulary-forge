from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forge  # noqa: E402
import integration_package  # noqa: E402


MANIFEST = ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"


class _ProjectMixin:
    def project(self, system_id: str = "openai_agents_contribution_work") -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name) / "project"
        forge.reconstruct_project(
            project=project,
            evidence_manifest=MANIFEST,
            system_id=system_id,
        )
        return project

    def document(self, project: Path, name: str) -> dict:
        return yaml.safe_load((project / "integration" / f"{name}.yaml").read_text(encoding="utf-8"))

    def tamper(self, project: Path, name: str, mutate) -> None:
        path = project / "integration" / f"{name}.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        manifest_path = project / "integration" / "integration_manifest.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][name]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


class IntegrationPackageLifecycleTests(_ProjectMixin, unittest.TestCase):
    def test_mapped_project_advances_to_integration_planned(self):
        project = self.project()
        result = forge.prepare_integration(project)
        persisted = forge.load_project(project)
        self.assertEqual(result["stage"], "integration_planned")
        self.assertEqual(persisted["stage"], "integration_planned")
        self.assertEqual(persisted["next_action"], "implement_and_verify_integration_contracts")

    def test_unmapped_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            with self.assertRaisesRegex(integration_package.IntegrationPackageError, "work_system_mapped"):
                forge.prepare_integration(project)

    def test_cli_prepares_one_selected_decision(self):
        project = self.project()
        status = forge.main(
            [
                "prepare-integration",
                str(project),
                "--decision",
                "choose_contribution_channel",
            ]
        )
        self.assertEqual(status, 0)
        package = self.document(project, "integration_manifest")
        self.assertEqual(
            [item["decision_id"] for item in package["selected_decisions"]],
            ["choose_contribution_channel"],
        )

    def test_unknown_decision_is_rejected_without_advancing_stage(self):
        project = self.project()
        with self.assertRaisesRegex(integration_package.IntegrationPackageError, "was not found"):
            forge.prepare_integration(project, decision_id="invented")
        self.assertEqual(forge.load_project(project)["stage"], "work_system_mapped")


class IntegrationPackageContractTests(_ProjectMixin, unittest.TestCase):
    def test_all_contracts_and_stable_ids_are_written(self):
        first = self.project()
        second = self.project()
        forge.prepare_integration(first)
        forge.prepare_integration(second)
        names = {
            "action_registry",
            "observable_state_adapter",
            "legal_action_policy",
            "controller_plan",
            "loop_plan",
            "verification_contract",
            "telemetry_contract",
        }
        self.assertTrue(all((first / "integration" / f"{name}.yaml").is_file() for name in names))
        first_actions = self.document(first, "action_registry")["actions"]
        second_actions = self.document(second, "action_registry")["actions"]
        self.assertEqual(
            [item["action_id"] for item in first_actions],
            [item["action_id"] for item in second_actions],
        )
        self.assertEqual(len({item["action_id"] for item in first_actions}), len(first_actions))

    def test_package_retains_hashed_work_map_and_source_inventory(self):
        project = self.project()
        work_map = yaml.safe_load((project / "work_system_map.yaml").read_text(encoding="utf-8"))
        result = forge.prepare_integration(project)
        self.assertEqual(result["source"]["source_inventory"], work_map["source_inventory"])
        self.assertEqual(
            result["source"]["sha256"],
            hashlib.sha256((project / "work_system_map.yaml").read_bytes()).hexdigest(),
        )

    def test_contracts_cover_adapter_policy_controller_loop_verification_and_telemetry(self):
        project = self.project()
        forge.prepare_integration(project)
        self.assertEqual(self.document(project, "observable_state_adapter")["status"], "implementation_required")
        self.assertIn("surfaces", self.document(project, "legal_action_policy"))
        self.assertEqual(self.document(project, "controller_plan")["action_execution"], "prohibited")
        self.assertIsNone(self.document(project, "loop_plan")["max_iterations"])
        self.assertTrue(self.document(project, "verification_contract")["checks"])
        self.assertTrue(self.document(project, "telemetry_contract")["required_fields"])


class IntegrationPackageSafetyTests(_ProjectMixin, unittest.TestCase):
    def test_reconstruction_actions_are_non_executable_stubs(self):
        project = self.project()
        forge.prepare_integration(project)
        for action in self.document(project, "action_registry")["actions"]:
            self.assertFalse(action["binding"]["executable"])
            self.assertEqual(action["binding"]["status"], "stub")
            self.assertEqual(action["binding"]["authority"], "none")
            self.assertIsNone(action["binding"]["locator"])
            self.assertEqual(action["legal"]["status"], "unverified")

    def test_no_fallback_or_activation_mapping_is_invented(self):
        project = self.project()
        forge.prepare_integration(project)
        for surface in self.document(project, "legal_action_policy")["surfaces"]:
            self.assertIsNone(surface["fallback_action_id"])
            self.assertEqual(surface["fallback_status"], "missing_reviewed_safe_fallback")
            self.assertEqual(surface["activation"], {
                "status": "unverified",
                "state_ids": [],
                "blocking_reason": "No reviewed runtime state mapping exists.",
            })

    def test_loop_is_blocked_and_cannot_execute(self):
        project = self.project()
        result = forge.prepare_integration(project)
        loop = self.document(project, "loop_plan")
        self.assertTrue(loop["status"].startswith("blocked_"))
        self.assertFalse(loop["executes_actions"])
        self.assertIsNone(loop["max_iterations"])
        self.assertFalse(result["safety"]["bindings_executable"])


class IntegrationPackageVerificationTests(_ProjectMixin, unittest.TestCase):
    def test_pristine_package_passes_without_executing_target(self):
        project = self.project()
        forge.prepare_integration(project)
        self.assertEqual(integration_package.verify_package(project), [])
        self.assertFalse(self.document(project, "integration_manifest")["safety"]["executes_actions"])

    def test_verifier_rejects_invalid_observation_shape(self):
        project = self.project()
        forge.prepare_integration(project)
        self.tamper(project, "observable_state_adapter", lambda value: value["output"].update(required_fields=[]))
        self.assertIn("observation adapter output contract is invalid", integration_package.verify_package(project))

    def test_verifier_rejects_action_outside_surface_registry(self):
        project = self.project()
        forge.prepare_integration(project)
        self.tamper(
            project,
            "legal_action_policy",
            lambda value: value["surfaces"][0]["candidate_action_ids"].append("invented"),
        )
        self.assertTrue(any("invalid action coverage" in error for error in integration_package.verify_package(project)))

    def test_verifier_rejects_executable_binding(self):
        project = self.project()
        forge.prepare_integration(project)
        self.tamper(
            project,
            "action_registry",
            lambda value: value["actions"][0]["binding"].update(executable=True, status="verified_runtime"),
        )
        self.assertTrue(any("unsafe binding" in error for error in integration_package.verify_package(project)))

    def test_verifier_rejects_unbounded_loop(self):
        project = self.project()
        forge.prepare_integration(project)
        self.tamper(project, "loop_plan", lambda value: value.update(status="ready", executes_actions=True))
        self.assertIn("loop plan does not fail closed", integration_package.verify_package(project))

    def test_verifier_rejects_incomplete_telemetry(self):
        project = self.project()
        forge.prepare_integration(project)
        self.tamper(project, "telemetry_contract", lambda value: value.update(required_fields=["event_id"]))
        self.assertIn("telemetry contract is invalid", integration_package.verify_package(project))

    def test_verifier_rejects_tampered_artifact_hash(self):
        project = self.project()
        forge.prepare_integration(project)
        path = project / "integration" / "controller_plan.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "unexpected: true\n", encoding="utf-8")
        self.assertIn("artifact hash mismatch for controller_plan", integration_package.verify_package(project))


if __name__ == "__main__":
    unittest.main()
