#!/usr/bin/env python3
"""Run the public-code-derived binding -> shadow controller benchmark."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import binding_verification  # noqa: E402
import forge  # noqa: E402
import shadow_controller  # noqa: E402


PUBLIC_MANIFEST = ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"
HARNESS = ROOT / "tests" / "fixtures" / "binding-verification-benchmark" / "public_stale_runtime.py"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _public_system() -> dict:
    manifest = yaml.safe_load(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    return next(
        item for item in manifest["systems"] if item["system_id"] == "openai_agents_contribution_work"
    )


def _evidence(source: dict, quote: str) -> dict:
    return {"source_id": source["source_id"], "source_sha256": source["sha256"], "quote": quote}


def _prepare_verified_binding(project: Path, workspace: Path) -> tuple[dict[str, str], str, dict]:
    forge.reconstruct_project(
        project=project,
        evidence_manifest=PUBLIC_MANIFEST,
        system_id="openai_agents_contribution_work",
    )
    forge.prepare_integration(project, decision_id="stale_item_policy")
    registry = yaml.safe_load((project / "integration" / "action_registry.yaml").read_text())
    actions = {item["label"]: item["action_id"] for item in registry["actions"]}
    policy = yaml.safe_load((project / "integration" / "legal_action_policy.yaml").read_text())
    surface_id = policy["surfaces"][0]["surface_id"]
    source = next(item for item in _public_system()["sources"] if item["source_id"] == "issues_workflow")
    binding_proposal = workspace / "binding_proposal.yaml"
    binding_proposal.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "system_id": "openai_agents_contribution_work",
                "bindings": [
                    {
                        "binding_id": "binding_mark_stale",
                        "action_id": actions["mark_stale"],
                        "kind": "python_callable",
                        "locator": {"target": "public_stale_runtime:mark_stale"},
                        "preconditions": ["issue has been inactive for at least seven days"],
                        "success_postconditions": ["the issue contains the stale label"],
                        "failure_postconditions": ["an ineligible issue is rejected without a state change"],
                        "evidence": _evidence(source, 'stale-issue-label: "stale"'),
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    binding_verification.propose_bindings(project=project, proposal=binding_proposal)
    candidate = yaml.safe_load((project / "integration" / "binding_candidates.yaml").read_text())["bindings"][0]
    common = {
        "binding_id": candidate["binding_id"],
        "binding_digest": candidate["binding_digest"],
        "action_id": candidate["action_id"],
        "test_run_id": "shadow-controller-benchmark-001",
        "environment_id": "isolated-public-code-derived-fixture",
        "invocation_fingerprint": _digest({"target": "mark_stale", "input": "redacted"}),
        "source_ref": "pinned-openai-agents-workflow/control-harness",
        "harness_id": "public-stale-runtime",
        "harness_sha256": hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
    }
    before = {"inactive_days": 8, "labels": []}
    after = {"inactive_days": 8, "labels": ["stale"]}
    negative = {"inactive_days": 2, "labels": []}
    observations = [
        {
            **common,
            "observation_id": "controller-success-001",
            "occurred_at": "2026-09-25T19:00:00Z",
            "case_type": "success",
            "state_before_fingerprint": _digest(before),
            "result_status": "succeeded",
            "state_after_fingerprint": _digest(after),
            "postcondition_id": "success_1",
            "postcondition_met": True,
        },
        {
            **common,
            "observation_id": "controller-negative-001",
            "occurred_at": "2026-09-25T19:00:01Z",
            "case_type": "negative",
            "state_before_fingerprint": _digest(negative),
            "result_status": "rejected",
            "state_after_fingerprint": _digest(negative),
            "postcondition_id": "failure_1",
            "postcondition_met": True,
        },
    ]
    observation_path = workspace / "binding_observations.jsonl"
    observation_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in observations),
        encoding="utf-8",
    )
    binding_verification.verify_bindings(project=project, observations=observation_path)
    return actions, surface_id, source


def evaluate() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        project = workspace / "project"
        actions, surface_id, source = _prepare_verified_binding(project, workspace)
        evidence = lambda quote: _evidence(source, quote)  # noqa: E731
        controller_proposal = workspace / "controller_proposal.yaml"
        controller_proposal.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.0",
                    "system_id": "openai_agents_contribution_work",
                    "states": [
                        {
                            "state_id": "inactive_eligible",
                            "label": "Inactive issue eligible for policy",
                            "predicate": "inactive_days >= 7 and closed == false",
                            "terminal": False,
                            "evidence": evidence("days-before-issue-stale: 7"),
                        },
                        {
                            "state_id": "terminal_closed",
                            "label": "Issue already closed",
                            "predicate": "closed == true",
                            "terminal": True,
                            "evidence": evidence("days-before-issue-close: 3"),
                        },
                    ],
                    "surface": {
                        "surface_id": surface_id,
                        "question_id": "inactive_item_action_v1",
                        "question": "Which reviewed inactive-item action best fits the observable issue state?",
                        "active_state_ids": ["inactive_eligible"],
                        "fallback_action_id": actions["skip_stale"],
                        "min_confidence": 0.8,
                        "actions": [
                            {
                                "action_id": actions["mark_stale"],
                                "allowed_state_ids": ["inactive_eligible"],
                                "preconditions": ["inactive_days >= 7", "closed == false"],
                                "evidence": evidence('stale-issue-label: "stale"'),
                            },
                            {
                                "action_id": actions["close"],
                                "allowed_state_ids": ["inactive_eligible"],
                                "preconditions": ["days_since_stale >= 3", "closed == false"],
                                "evidence": evidence("days-before-issue-close: 3"),
                            },
                            {
                                "action_id": actions["skip_stale"],
                                "allowed_state_ids": ["inactive_eligible"],
                                "preconditions": [],
                                "evidence": evidence('exempt-issue-labels: "skip-stale"'),
                            },
                        ],
                    },
                    "loop": {
                        "max_iterations": 3,
                        "stop_conditions": sorted(shadow_controller.REQUIRED_STOP_CONDITIONS),
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        shadow_controller.prepare_controller(project=project, proposal=controller_proposal)
        runtime = shadow_controller.load_runtime(project)
        offered: list[list[str]] = []

        def jev_shaped_decider(_question, _state, legal_actions):
            offered.append(list(legal_actions))
            return {"answer": actions["mark_stale"], "confidence": 0.93}

        run = runtime.run(
            [
                {"inactive_days": 8, "days_since_stale": 0, "closed": False},
                {"closed": True},
            ],
            jev_shaped_decider,
        )
        low_confidence = runtime.run(
            [{"inactive_days": 8, "days_since_stale": 0, "closed": False}],
            lambda *_args: {"answer": actions["mark_stale"], "confidence": 0.4},
        )
        legal_only = bool(offered) and offered[0] == [actions["mark_stale"], actions["skip_stale"]]
        passed = (
            not shadow_controller.verify_controller(project)
            and run["stop_reason"] == "terminal_state"
            and run["binding_invocations"] == 0
            and run["records"][0]["selected_action_id"] in offered[0]
            and legal_only
            and low_confidence["stop_reason"] == "low_confidence"
            and low_confidence["records"][0]["selected_action_id"] == actions["skip_stale"]
            and low_confidence["binding_invocations"] == 0
        )
        system = _public_system()
        return {
            "status": "PASS" if passed else "HOLD",
            "public_repository": system["repository"],
            "public_commit": system["commit"],
            "controller_stage": forge.load_project(project)["stage"],
            "decider_calls": len(offered),
            "legal_actions_offered": len(offered[0]) if offered else 0,
            "illegal_actions_offered": 0 if legal_only else 1,
            "terminal_stop": run["stop_reason"],
            "low_confidence_stop": low_confidence["stop_reason"],
            "binding_invocations": run["binding_invocations"] + low_confidence["binding_invocations"],
            "production_authority": False,
        }


def main() -> int:
    report = evaluate()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
