from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import binding_verification  # noqa: E402
import forge  # noqa: E402
import integration_package  # noqa: E402


MANIFEST = ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"
HARNESS = ROOT / "tests" / "fixtures" / "binding-verification-benchmark" / "public_stale_runtime.py"


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class BindingVerificationMixin:
    def project(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name) / "project"
        forge.reconstruct_project(
            project=project,
            evidence_manifest=MANIFEST,
            system_id="openai_agents_contribution_work",
        )
        forge.prepare_integration(project, decision_id="stale_item_policy")
        return project

    def stale_action_id(self, project: Path) -> str:
        registry = yaml.safe_load((project / "integration" / "action_registry.yaml").read_text())
        return next(item["action_id"] for item in registry["actions"] if item["label"] == "mark_stale")

    def proposal(self, project: Path, **binding_changes) -> Path:
        action_id = self.stale_action_id(project)
        source = yaml.safe_load(MANIFEST.read_text())
        system = next(item for item in source["systems"] if item["system_id"] == "openai_agents_contribution_work")
        evidence = next(item for item in system["sources"] if item["source_id"] == "issues_workflow")
        binding = {
            "binding_id": "binding_mark_stale",
            "action_id": action_id,
            "kind": "python_callable",
            "locator": {"target": "public_stale_runtime:mark_stale"},
            "preconditions": ["issue has been inactive for at least seven days"],
            "success_postconditions": ["the issue contains the stale label"],
            "failure_postconditions": ["an ineligible issue is rejected without a state change"],
            "evidence": {
                "source_id": "issues_workflow",
                "source_sha256": evidence["sha256"],
                "quote": 'stale-issue-label: "stale"',
            },
        }
        binding.update(binding_changes)
        proposal = {
            "schema_version": "1.0",
            "system_id": "openai_agents_contribution_work",
            "bindings": [binding],
        }
        path = project / "binding_proposal.yaml"
        path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
        return path

    def propose(self, project: Path, **binding_changes) -> dict:
        return binding_verification.propose_bindings(
            project=project,
            proposal=self.proposal(project, **binding_changes),
        )

    def observations(self, project: Path, *, include_negative: bool = True, **changes) -> Path:
        candidate = yaml.safe_load((project / "integration" / "binding_candidates.yaml").read_text())["bindings"][0]
        before = {"inactive_days": 8, "labels": []}
        after = {"inactive_days": 8, "labels": ["stale"]}
        common = {
            "binding_id": candidate["binding_id"],
            "binding_digest": candidate["binding_digest"],
            "action_id": candidate["action_id"],
            "test_run_id": "run-public-stale-001",
            "environment_id": "isolated-public-fixture",
            "invocation_fingerprint": _fingerprint({"target": "mark_stale", "args": "redacted"}),
            "source_ref": "controlled-harness/public-stale-runtime",
            "harness_id": "binding-verification-benchmark",
            "harness_sha256": hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
        }
        success = {
            **common,
            "observation_id": "obs-success-001",
            "occurred_at": "2026-09-25T18:00:00Z",
            "case_type": "success",
            "state_before_fingerprint": _fingerprint(before),
            "result_status": "succeeded",
            "state_after_fingerprint": _fingerprint(after),
            "postcondition_id": "success_1",
            "postcondition_met": True,
        }
        negative = {
            **common,
            "observation_id": "obs-negative-001",
            "occurred_at": "2026-09-25T18:00:01Z",
            "case_type": "negative",
            "state_before_fingerprint": _fingerprint({"inactive_days": 2, "labels": []}),
            "result_status": "rejected",
            "state_after_fingerprint": _fingerprint({"inactive_days": 2, "labels": []}),
            "postcondition_id": "failure_1",
            "postcondition_met": True,
        }
        success.update(changes)
        events = [success, negative] if include_negative else [success]
        path = project / "binding_observations.jsonl"
        path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in events), encoding="utf-8")
        return path


class BindingProposalTests(BindingVerificationMixin, unittest.TestCase):
    def test_proposal_advances_stage_without_executing_target(self):
        project = self.project()
        result = self.propose(project)
        persisted = forge.load_project(project)
        self.assertEqual(result["stage"], "binding_candidates_ready")
        self.assertEqual(persisted["stage"], "binding_candidates_ready")
        self.assertFalse(result["safety"]["executes_actions"])
        self.assertFalse(result["safety"]["production_authority"])
        self.assertTrue((project / "integration" / "binding_observation_schema.json").is_file())

    def test_candidate_has_stable_digest_and_typed_locator(self):
        project = self.project()
        self.propose(project)
        document = yaml.safe_load((project / "integration" / "binding_candidates.yaml").read_text())
        candidate = document["bindings"][0]
        self.assertRegex(candidate["binding_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(candidate["locator"], {"target": "public_stale_runtime:mark_stale"})
        self.assertEqual(candidate["status"], "candidate")
        self.assertFalse(candidate["executable"])

    def test_unknown_action_fails_closed(self):
        project = self.project()
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "unknown action_id"):
            self.propose(project, action_id="invented")
        self.assertEqual(forge.load_project(project)["stage"], "integration_planned")

    def test_quote_must_exist_in_hashed_source(self):
        project = self.project()
        evidence = self.proposal(project)
        value = yaml.safe_load(evidence.read_text())
        value["bindings"][0]["evidence"]["quote"] = "invented operation"
        evidence.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "quote is not present"):
            binding_verification.propose_bindings(project=project, proposal=evidence)

    def test_changed_source_hash_fails_closed(self):
        project = self.project()
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "source_sha256"):
            self.propose(project, evidence={
                "source_id": "issues_workflow",
                "source_sha256": "0" * 64,
                "quote": 'stale-issue-label: "stale"',
            })

    def test_shell_string_and_embedded_secret_are_rejected(self):
        project = self.project()
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "argv"):
            self.propose(project, kind="cli", locator={"argv": "sh -c dangerous"})
        project = self.project()
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "secret"):
            self.propose(project, locator={"target": "public_stale_runtime:mark_stale", "token": "secret-value"})


class BindingObservationTests(BindingVerificationMixin, unittest.TestCase):
    def test_success_and_negative_observations_promote_shadow_only(self):
        project = self.project()
        self.propose(project)
        result = binding_verification.verify_bindings(project=project, observations=self.observations(project))
        self.assertEqual(result["stage"], "bindings_verified_for_shadow")
        self.assertEqual(result["verified_binding_count"], 1)
        registry = yaml.safe_load((project / "integration" / "action_registry.yaml").read_text())
        binding = next(item["binding"] for item in registry["actions"] if item["label"] == "mark_stale")
        self.assertEqual(binding["status"], "verified_for_shadow")
        self.assertEqual(binding["evidence_grade"], "observed_trace")
        self.assertFalse(binding["executable"])
        self.assertFalse(binding["production_authority"])
        self.assertEqual(binding["authority"], "none")
        self.assertEqual(integration_package.verify_package(project), [])
        self.assertEqual(binding_verification.verify_binding_state(project), [])

    def test_archived_evidence_contains_fingerprints_not_raw_state(self):
        project = self.project()
        self.propose(project)
        binding_verification.verify_bindings(project=project, observations=self.observations(project))
        archive = (project / "integration" / "evidence" / "binding_observations.jsonl").read_text()
        self.assertNotIn("inactive_days", archive)
        self.assertNotIn("labels", archive)
        self.assertNotIn("chain_of_thought", archive)
        verification = yaml.safe_load((project / "integration" / "binding_verification.yaml").read_text())
        self.assertRegex(verification["observation_source"]["sha256"], r"^[0-9a-f]{64}$")

    def test_success_only_evidence_is_rejected_without_promotion(self):
        project = self.project()
        self.propose(project)
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "negative"):
            binding_verification.verify_bindings(
                project=project,
                observations=self.observations(project, include_negative=False),
            )
        self.assertEqual(forge.load_project(project)["stage"], "binding_candidates_ready")

    def test_duplicate_observation_is_rejected(self):
        project = self.project()
        self.propose(project)
        path = self.observations(project)
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[1]["observation_id"] = events[0]["observation_id"]
        path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "duplicate observation_id"):
            binding_verification.verify_bindings(project=project, observations=path)

    def test_unknown_binding_and_changed_digest_are_rejected(self):
        project = self.project()
        self.propose(project)
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "unknown binding_id"):
            binding_verification.verify_bindings(
                project=project,
                observations=self.observations(project, binding_id="invented"),
            )
        project = self.project()
        self.propose(project)
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "binding_digest"):
            binding_verification.verify_bindings(
                project=project,
                observations=self.observations(project, binding_digest="0" * 64),
            )

    def test_raw_state_private_reasoning_and_secrets_are_rejected(self):
        for field, value, message in (
            ("state_before", {"value": 1}, "unsupported fields"),
            ("chain_of_thought", "private", "unsupported fields"),
            ("source_ref", "Bearer abc123", "secret"),
        ):
            with self.subTest(field=field):
                project = self.project()
                self.propose(project)
                with self.assertRaisesRegex(binding_verification.BindingVerificationError, message):
                    binding_verification.verify_bindings(
                        project=project,
                        observations=self.observations(project, **{field: value}),
                    )

    def test_malformed_success_transition_is_rejected(self):
        project = self.project()
        self.propose(project)
        before = _fingerprint({"same": True})
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "must change observable state"):
            binding_verification.verify_bindings(
                project=project,
                observations=self.observations(project, state_before_fingerprint=before, state_after_fingerprint=before),
            )

    def test_negative_case_must_leave_state_unchanged(self):
        project = self.project()
        self.propose(project)
        path = self.observations(project)
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[1]["state_after_fingerprint"] = _fingerprint({"mutated": True})
        path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "leave observable state unchanged"):
            binding_verification.verify_bindings(project=project, observations=path)

    def test_success_and_negative_must_use_same_harness_digest(self):
        project = self.project()
        self.propose(project)
        path = self.observations(project)
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[1]["harness_sha256"] = "1" * 64
        path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "same harness digest"):
            binding_verification.verify_bindings(project=project, observations=path)

    def test_candidate_tampering_is_detected(self):
        project = self.project()
        self.propose(project)
        candidates = project / "integration" / "binding_candidates.yaml"
        candidates.write_text(candidates.read_text() + "tampered: true\n", encoding="utf-8")
        with self.assertRaisesRegex(binding_verification.BindingVerificationError, "binding candidate hash mismatch"):
            binding_verification.verify_bindings(project=project, observations=self.observations(project))

    def test_promoted_registry_cannot_drift_from_verified_candidate(self):
        project = self.project()
        self.propose(project)
        binding_verification.verify_bindings(project=project, observations=self.observations(project))
        registry_path = project / "integration" / "action_registry.yaml"
        registry = yaml.safe_load(registry_path.read_text())
        target = next(item for item in registry["actions"] if item["label"] == "mark_stale")
        target["binding"]["locator"] = {"target": "public_stale_runtime:other"}
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        integration_manifest_path = project / "integration" / "integration_manifest.yaml"
        integration_manifest = yaml.safe_load(integration_manifest_path.read_text())
        integration_manifest["artifacts"]["action_registry"]["sha256"] = hashlib.sha256(
            registry_path.read_bytes()
        ).hexdigest()
        integration_manifest_path.write_text(
            yaml.safe_dump(integration_manifest, sort_keys=False), encoding="utf-8"
        )
        self.assertIn(
            "binding binding_mark_stale: promoted action binding does not match verified candidate",
            binding_verification.verify_binding_state(project),
        )


class BindingCliTests(BindingVerificationMixin, unittest.TestCase):
    def test_cli_propose_and_verify_lifecycle(self):
        project = self.project()
        self.assertEqual(forge.main(["propose-bindings", str(project), "--proposal", str(self.proposal(project))]), 0)
        self.assertEqual(
            forge.main(["verify-bindings", str(project), "--observations", str(self.observations(project))]),
            0,
        )
        self.assertEqual(forge.load_project(project)["stage"], "bindings_verified_for_shadow")


if __name__ == "__main__":
    unittest.main()
