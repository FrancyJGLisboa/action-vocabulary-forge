from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from typing import Iterator

import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import acquire_work_system_evidence as acquisition  # noqa: E402
import forge  # noqa: E402
import direct_acquisition_benchmark as direct_benchmark  # noqa: E402


def _write_material(root: Path) -> None:
    (root / "CONTRIBUTING.md").write_text(
        "Maintainers review each contribution.\n"
        "Choose exactly one outcome: accept or revise.\n",
        encoding="utf-8",
    )
    workflow = root / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "review.yml").write_text(
        "name: review\n"
        "on: pull_request\n"
        "jobs:\n  validate:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )


def _proposal_for(project: Path) -> dict:
    evidence_manifest = yaml.safe_load((project / "evidence_manifest.yaml").read_text(encoding="utf-8"))
    sources = evidence_manifest["systems"][0]["sources"]
    first = next(item for item in sources if item["kind"] == "policy_document")
    second = next(item for item in sources if item["kind"] == "deterministic_workflow")
    first_text = (project / first["file"]).read_text(encoding="utf-8")
    second_text = (project / second["file"]).read_text(encoding="utf-8")
    first_quote = first_text.splitlines()[0]
    second_quote = second_text.splitlines()[0]
    system_id = evidence_manifest["systems"][0]["system_id"]
    return {
        "schema_version": "1.0",
        "system_id": system_id,
        "nodes": [
            {
                "node_id": "maintainer",
                "kind": "actor",
                "label": "Maintainer",
                "evidence": [{"source_id": first["source_id"], "quote": first_quote, "status": "declared"}],
            },
            {
                "node_id": "contribution",
                "kind": "artifact",
                "label": "Contribution",
                "evidence": [{"source_id": first["source_id"], "quote": first_quote, "status": "declared"}],
            },
            {
                "node_id": "validate",
                "kind": "activity",
                "label": "Validate",
                "evidence": [{"source_id": second["source_id"], "quote": second_quote, "status": "declared"}],
            },
            {
                "node_id": "review_outcome",
                "kind": "decision",
                "label": "Review outcome",
                "candidate_actions": ["accept", "revise"],
                "evidence": [
                    {
                        "source_id": first["source_id"],
                        "quote": "Choose exactly one outcome: accept or revise.",
                        "status": "declared",
                    }
                ],
            },
        ],
        "links": [],
        "workflows": [
            {
                "workflow_id": "contribution_review",
                "label": "Contribution review",
                "steps": ["contribution", "validate", "review_outcome"],
                "actor_ids": ["maintainer"],
                "artifact_ids": ["contribution"],
                "decision_ids": ["review_outcome"],
                "source_ids": [first["source_id"], second["source_id"]],
                "repeated_case_count": 0,
                "gaps": ["No resolved cases were supplied."],
            }
        ],
    }


class DirectEvidenceAcquisitionContractTests(unittest.TestCase):
    def test_local_material_becomes_hashed_reviewable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "material"
            material.mkdir()
            _write_material(material)
            project = root / "project"

            manifest = forge.acquire_reconstruction_project(
                project=project,
                system_id="contribution_work",
                sources=[material],
            )

            self.assertEqual(manifest["stage"], "evidence_inventoried")
            evidence = yaml.safe_load((project / "evidence_manifest.yaml").read_text(encoding="utf-8"))
            sources = evidence["systems"][0]["sources"]
            self.assertEqual(len(sources), 2)
            self.assertEqual({item["kind"] for item in sources}, {"policy_document", "deterministic_workflow"})
            for item in sources:
                self.assertEqual(len(item["sha256"]), 64)
                self.assertEqual(item["evidence_status"], "declared")
                self.assertEqual(item["origin"]["type"], "local")
                self.assertTrue((project / item["file"]).is_file())

    def test_repository_commit_and_origin_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repo"
            repository.mkdir()
            _write_material(repository)

            @contextlib.contextmanager
            def fake_repository(_url: str) -> Iterator[acquisition.RepositorySnapshot]:
                yield acquisition.RepositorySnapshot(
                    root=repository,
                    repository="https://github.com/example/project",
                    commit="a" * 40,
                )

            project = root / "project"
            forge.acquire_reconstruction_project(
                project=project,
                system_id="repo_work",
                repositories=["https://github.com/example/project"],
                repository_materializer=fake_repository,
            )
            system = yaml.safe_load((project / "evidence_manifest.yaml").read_text(encoding="utf-8"))["systems"][0]
            self.assertEqual(system["repository"], "https://github.com/example/project")
            self.assertEqual(system["commit"], "a" * 40)
            self.assertTrue(all(item["origin"]["commit"] == "a" * 40 for item in system["sources"]))
            self.assertTrue(all("/blob/" + "a" * 40 + "/" in item["origin"]["locator"] for item in system["sources"]))

    def test_url_content_is_snapshotted_with_original_locator(self):
        def fetcher(url: str, _max_bytes: int) -> tuple[bytes, str]:
            self.assertEqual(url, "https://example.test/operations.md")
            return b"Operators choose accept or revise.\n", "text/markdown"

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            forge.acquire_reconstruction_project(
                project=project,
                system_id="url_work",
                urls=["https://example.test/operations.md"],
                url_fetcher=fetcher,
            )
            source = yaml.safe_load((project / "evidence_manifest.yaml").read_text(encoding="utf-8"))["systems"][0]["sources"][0]
            self.assertEqual(source["origin"], {"type": "url", "locator": "https://example.test/operations.md"})
            self.assertEqual(source["bytes"], 35)


class DirectEvidenceAcquisitionLifecycleTests(unittest.TestCase):
    def test_inventory_stage_writes_request_and_template_without_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "material"
            material.mkdir()
            _write_material(material)
            project = root / "project"
            manifest = forge.acquire_reconstruction_project(
                project=project,
                system_id="contribution_work",
                sources=[material],
            )
            self.assertEqual(manifest["next_action"], "complete_system2_reconstruction")
            self.assertTrue((project / "reconstruction_request.md").is_file())
            self.assertTrue((project / "work_system_proposal.yaml").is_file())
            self.assertFalse(manifest["safety"]["shadow_eligible"])
            self.assertFalse(manifest["safety"]["production_authority"])
            self.assertFalse(manifest["safety"]["executes_actions"])
            self.assertTrue(manifest["safety"]["raw_source_content_persisted"])
            request = (project / "reconstruction_request.md").read_text(encoding="utf-8")
            self.assertIn("System 2", request)
            self.assertIn("exact quotes", request)
            self.assertIn("does not authorize", request)


class DirectEvidenceAcquisitionFinalizeTests(unittest.TestCase):
    def test_system2_proposal_finalizes_same_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "material"
            material.mkdir()
            _write_material(material)
            project = root / "project"
            forge.acquire_reconstruction_project(
                project=project,
                system_id="contribution_work",
                sources=[material],
            )
            proposal = _proposal_for(project)
            (project / "work_system_proposal.yaml").write_text(
                yaml.safe_dump(proposal, sort_keys=False),
                encoding="utf-8",
            )

            manifest = forge.finalize_acquired_reconstruction(project=project)

            self.assertEqual(manifest["stage"], "work_system_mapped")
            self.assertEqual(manifest["summary"]["workflows"], 1)
            self.assertEqual(manifest["summary"]["decision_candidates"], 1)
            self.assertTrue((project / "work_system_map.yaml").is_file())
            self.assertFalse(manifest["safety"]["shadow_eligible"])

    def test_invalid_system2_quote_is_rejected_without_advancing_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "material"
            material.mkdir()
            _write_material(material)
            project = root / "project"
            forge.acquire_reconstruction_project(project=project, system_id="work", sources=[material])
            proposal = _proposal_for(project)
            proposal["nodes"][0]["evidence"][0]["quote"] = "not in the source"
            (project / "work_system_proposal.yaml").write_text(yaml.safe_dump(proposal), encoding="utf-8")
            with self.assertRaisesRegex(Exception, "quote is not present"):
                forge.finalize_acquired_reconstruction(project=project)
            self.assertEqual(forge.load_project(project)["stage"], "evidence_inventoried")


class DirectEvidenceAcquisitionSafetyTests(unittest.TestCase):
    def test_non_https_url_is_rejected_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(acquisition.AcquisitionError, "HTTPS"):
                forge.acquire_reconstruction_project(
                    project=Path(directory) / "project",
                    system_id="unsafe",
                    urls=["http://example.test/source.md"],
                )

    def test_non_github_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(acquisition.AcquisitionError, "GitHub HTTPS"):
                forge.acquire_reconstruction_project(
                    project=Path(directory) / "project",
                    system_id="unsafe",
                    repositories=["https://gitlab.com/example/project"],
                )

    def test_symlink_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.md"
            target.write_text("text", encoding="utf-8")
            link = root / "link.md"
            link.symlink_to(target)
            with self.assertRaisesRegex(acquisition.AcquisitionError, "symlink"):
                forge.acquire_reconstruction_project(
                    project=root / "project",
                    system_id="unsafe",
                    sources=[link],
                )

    def test_binary_oversized_and_duplicate_material_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "material"
            material.mkdir()
            good = material / "SOP.md"
            good.write_text("Choose accept or revise.\n", encoding="utf-8")
            (material / "binary.md").write_bytes(b"\x00\x01")
            (material / "large.md").write_text("x" * 80, encoding="utf-8")
            project = root / "project"
            forge.acquire_reconstruction_project(
                project=project,
                system_id="bounded",
                sources=[material, good],
                max_source_bytes=64,
            )
            catalog = yaml.safe_load((project / "evidence" / "source_catalog.yaml").read_text(encoding="utf-8"))
            reasons = {item["reason"] for item in catalog["skipped"]}
            self.assertEqual(catalog["included_count"], 1)
            self.assertTrue({"binary", "oversized", "duplicate"} <= reasons)

    def test_empty_supported_evidence_set_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty"
            empty.mkdir()
            (empty / "image.png").write_bytes(b"png")
            with self.assertRaisesRegex(acquisition.AcquisitionError, "no supported textual evidence"):
                forge.acquire_reconstruction_project(
                    project=root / "project",
                    system_id="empty",
                    sources=[empty],
                )


class DirectEvidenceAcquisitionCLITests(unittest.TestCase):
    def test_reconstruct_parser_accepts_direct_inputs(self):
        parsed = forge.parser().parse_args(
            [
                "reconstruct",
                "--project",
                "out",
                "--system-id",
                "work",
                "--repo",
                "https://github.com/example/project",
                "--url",
                "https://example.test/sop.md",
                "--source",
                "local",
            ]
        )
        self.assertEqual(parsed.repo, ["https://github.com/example/project"])
        self.assertEqual(parsed.url, ["https://example.test/sop.md"])
        self.assertEqual(parsed.source, [Path("local")])

    def test_cli_direct_source_creates_inventory_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "material"
            material.mkdir()
            _write_material(material)
            project = root / "project"
            status = forge.main(
                [
                    "reconstruct",
                    "--project",
                    str(project),
                    "--system-id",
                    "cli_work",
                    "--source",
                    str(material),
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(forge.load_project(project)["stage"], "evidence_inventoried")

    def test_existing_manifest_cli_remains_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "legacy-project"
            status = forge.main(
                [
                    "reconstruct",
                    "--project",
                    str(project),
                    "--evidence-manifest",
                    str(ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"),
                    "--system-id",
                    "pydantic_ai_contribution_work",
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(forge.load_project(project)["stage"], "work_system_mapped")


class DirectEvidenceAcquisitionBenchmarkTests(unittest.TestCase):
    def test_two_public_systems_are_deterministic_and_match_pinned_hashes(self):
        report = direct_benchmark.evaluate()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["systems"]), 2)
        self.assertTrue(all(item["deterministic"] for item in report["systems"]))
        self.assertTrue(all(item["provenance_match"] for item in report["systems"]))


if __name__ == "__main__":
    unittest.main()
