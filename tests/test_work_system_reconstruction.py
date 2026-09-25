from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import reconstruct_work_system as reconstruction  # noqa: E402
import work_system_benchmark as benchmark  # noqa: E402
import forge  # noqa: E402


class WorkSystemReconstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = benchmark.load_manifest()
        self.system = self.manifest["systems"][0]
        self.proposal = reconstruction.load_yaml(
            benchmark.FIXTURE_ROOT / self.system["proposal_file"]
        )

    def reconstruct(self, proposal: dict):
        return reconstruction.reconstruct(
            self.system,
            root=benchmark.FIXTURE_ROOT,
            proposal_override=proposal,
        )

    def test_two_public_systems_reconstruct_cross_source_workflows(self):
        report = benchmark.evaluate(self.manifest)
        self.assertEqual(report["coverage"], {"status": "PASS", "systems": 2, "repositories": 2})
        self.assertEqual(report["status"], "PASS")
        self.assertEqual([row["nodes"] for row in report["systems"]], [12, 13])
        self.assertEqual([row["decisions"] for row in report["systems"]], [2, 3])
        self.assertTrue(benchmark.verify_contract(report))
        self.assertTrue(benchmark.verify_cross_source(report))

    def test_source_quote_must_exist(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["nodes"][0]["evidence"][0]["quote"] = "invented unsupported claim"
        with self.assertRaisesRegex(reconstruction.ReconstructionError, "quote is not present"):
            self.reconstruct(proposal)

    def test_declared_source_cannot_be_promoted_to_observed(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["nodes"][0]["evidence"][0]["status"] = "observed"
        with self.assertRaisesRegex(reconstruction.ReconstructionError, "cannot prove observed behavior"):
            self.reconstruct(proposal)

    def test_unknown_link_endpoint_is_rejected(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["links"][0]["to"] = "imaginary_state"
        with self.assertRaisesRegex(reconstruction.ReconstructionError, "known nodes"):
            self.reconstruct(proposal)

    def test_workflow_without_explicit_evidence_gap_is_rejected(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["workflows"][0]["gaps"] = []
        with self.assertRaisesRegex(reconstruction.ReconstructionError, "explicit evidence gaps"):
            self.reconstruct(proposal)

    def test_unknown_source_reference_is_rejected(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["nodes"][0]["evidence"][0]["source_id"] = "private_memory"
        with self.assertRaisesRegex(reconstruction.ReconstructionError, "unknown source reference"):
            self.reconstruct(proposal)

    def test_tampered_source_hash_is_rejected(self):
        system = copy.deepcopy(self.system)
        system["sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(reconstruction.ReconstructionError, "source hash mismatch"):
            reconstruction.validate_system_spec(system, root=benchmark.FIXTURE_ROOT)

    def test_output_never_grants_runtime_authority(self):
        result = self.reconstruct(copy.deepcopy(self.proposal))
        self.assertTrue(result["safety"]["descriptive_only"])
        self.assertFalse(result["safety"]["inference_is_authority"])
        self.assertFalse(result["safety"]["executes_actions"])
        self.assertFalse(result["safety"]["production_authority"])
        self.assertTrue(all(not item["shadow_eligible"] for item in result["decision_candidates"]))

    def test_forge_cli_stage_persists_a_reviewable_work_system_project(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "forge-project"
            manifest = forge.reconstruct_project(
                project=project,
                evidence_manifest=benchmark.FIXTURE_ROOT / "manifest.yaml",
                system_id="pydantic_ai_contribution_work",
            )
            self.assertEqual(manifest["stage"], "work_system_mapped")
            self.assertEqual(manifest["next_action"], "review_decision_opportunities")
            self.assertTrue((project / "work_system_map.yaml").is_file())
            self.assertTrue((project / "work_system_review.md").is_file())
            self.assertFalse(manifest["safety"]["shadow_eligible"])
            status = forge.render_status(project)
            self.assertIn("Reconstructed workflows: 1", status)
            self.assertIn("Decision candidates: 2", status)


if __name__ == "__main__":
    unittest.main()
