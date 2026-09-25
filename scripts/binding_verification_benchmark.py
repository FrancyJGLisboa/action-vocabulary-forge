#!/usr/bin/env python3
"""Exercise binding verification with a controlled adapter derived from public code."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import binding_verification  # noqa: E402
import forge  # noqa: E402
import integration_package  # noqa: E402


PUBLIC_MANIFEST = ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"
HARNESS = ROOT / "tests" / "fixtures" / "binding-verification-benchmark" / "public_stale_runtime.py"


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("public_stale_runtime", HARNESS)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load controlled binding benchmark harness")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = root / "project"
        forge.reconstruct_project(
            project=project,
            evidence_manifest=PUBLIC_MANIFEST,
            system_id="openai_agents_contribution_work",
        )
        forge.prepare_integration(project, decision_id="stale_item_policy")
        registry = yaml.safe_load((project / "integration" / "action_registry.yaml").read_text())
        action_id = next(item["action_id"] for item in registry["actions"] if item["label"] == "mark_stale")
        public = yaml.safe_load(PUBLIC_MANIFEST.read_text())
        system = next(item for item in public["systems"] if item["system_id"] == "openai_agents_contribution_work")
        source = next(item for item in system["sources"] if item["source_id"] == "issues_workflow")
        proposal_path = root / "binding_proposal.yaml"
        proposal_path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.0",
                    "system_id": "openai_agents_contribution_work",
                    "bindings": [
                        {
                            "binding_id": "binding_mark_stale",
                            "action_id": action_id,
                            "kind": "python_callable",
                            "locator": {"target": "public_stale_runtime:mark_stale"},
                            "preconditions": ["issue has been inactive for at least seven days"],
                            "success_postconditions": ["the issue contains the stale label"],
                            "failure_postconditions": ["an ineligible issue is rejected without a state change"],
                            "evidence": {
                                "source_id": "issues_workflow",
                                "source_sha256": source["sha256"],
                                "quote": 'stale-issue-label: "stale"',
                            },
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        binding_verification.propose_bindings(project=project, proposal=proposal_path)
        candidate = yaml.safe_load((project / "integration" / "binding_candidates.yaml").read_text())["bindings"][0]

        # This is the controlled host boundary. The Forge itself never imports or calls the target.
        harness = _load_harness()
        before_success = {"inactive_days": 8, "labels": []}
        after_success = harness.mark_stale(before_success)
        before_negative = {"inactive_days": 2, "labels": []}
        negative_status = "succeeded"
        try:
            harness.mark_stale(before_negative)
        except ValueError:
            negative_status = "rejected"
        common = {
            "binding_id": candidate["binding_id"],
            "binding_digest": candidate["binding_digest"],
            "action_id": candidate["action_id"],
            "test_run_id": "public-stale-benchmark-001",
            "environment_id": "isolated-public-code-derived-fixture",
            "invocation_fingerprint": _digest({"target": "mark_stale", "input": "redacted"}),
            "source_ref": "pinned-openai-agents-workflow/control-harness",
            "harness_id": "public-stale-runtime",
            "harness_sha256": hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
        }
        observations = [
            {
                **common,
                "observation_id": "public-success-001",
                "occurred_at": "2026-09-25T18:00:00Z",
                "case_type": "success",
                "state_before_fingerprint": _digest(before_success),
                "result_status": "succeeded",
                "state_after_fingerprint": _digest(after_success),
                "postcondition_id": "success_1",
                "postcondition_met": "stale" in after_success["labels"],
            },
            {
                **common,
                "observation_id": "public-negative-001",
                "occurred_at": "2026-09-25T18:00:01Z",
                "case_type": "negative",
                "state_before_fingerprint": _digest(before_negative),
                "result_status": negative_status,
                "state_after_fingerprint": _digest(before_negative),
                "postcondition_id": "failure_1",
                "postcondition_met": negative_status == "rejected",
            },
        ]
        observation_path = root / "observations.jsonl"
        observation_path.write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in observations),
            encoding="utf-8",
        )
        result = binding_verification.verify_bindings(project=project, observations=observation_path)
        return {
            "status": "PASS"
            if not binding_verification.verify_binding_state(project)
            and not integration_package.verify_package(project)
            and result["verified_binding_count"] == 1
            and result["safety"]["forge_executed_target"] is False
            and result["safety"]["bindings_executable"] is False
            else "HOLD",
            "public_repository": system["repository"],
            "public_commit": system["commit"],
            "controlled_harness_executed": True,
            "forge_executed_target": False,
            "successful_observations": 1,
            "negative_observations": 1,
            "verified_shadow_bindings": result["verified_binding_count"],
            "executable_bindings": 0,
            "production_authority": False,
        }


def main() -> int:
    report = evaluate()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
