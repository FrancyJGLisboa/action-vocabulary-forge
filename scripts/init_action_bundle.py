#!/usr/bin/env python3
"""Create an empty or illustrative Action Bundle."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml


FILES = [
    "surface_candidates.yaml",
    "action_registry.yaml",
    "state_registry.yaml",
    "transition_graph.yaml",
    "decision_surfaces.yaml",
    "evidence_ledger.jsonl",
    "jev_adapter_spec.yaml",
    "coverage_report.md",
]


def example_bundle() -> dict[str, Any]:
    evidence = [
        {
            "evidence_id": "runbook_retry",
            "source_id": "docs/validation.md#retry",
            "source_type": "sop",
            "locator": "docs/validation.md#retry",
            "claim": "Transient validation errors may be retried.",
            "grade": "documented",
            "observed_at": None,
            "notes": "",
        },
        {
            "evidence_id": "runbook_review",
            "source_id": "docs/validation.md#review",
            "source_type": "sop",
            "locator": "docs/validation.md#review",
            "claim": "Ambiguous validation failures require human review.",
            "grade": "documented",
            "observed_at": None,
            "notes": "",
        },
        {
            "evidence_id": "trace_retry_01",
            "source_id": "trace:validation/001",
            "source_type": "observed_trace",
            "locator": "trace:validation/001",
            "claim": "A transient timeout was retried from validation_failed.",
            "grade": "observed_trace",
            "observed_at": "2026-09-21T00:00:00Z",
            "notes": "",
        },
        {
            "evidence_id": "trace_review_01",
            "source_id": "trace:validation/002",
            "source_type": "observed_trace",
            "locator": "trace:validation/002",
            "claim": "An ambiguous schema error was routed to review.",
            "grade": "observed_trace",
            "observed_at": "2026-09-21T00:00:00Z",
            "notes": "",
        },
        {
            "evidence_id": "trace_pending_01",
            "source_id": "trace:validation/000",
            "source_type": "verified_runtime",
            "locator": "trace:validation/000",
            "claim": "Validation can enter a pending state.",
            "grade": "verified_runtime",
            "observed_at": "2026-09-21T00:00:00Z",
            "notes": "",
        },
        {
            "evidence_id": "trace_failed_01",
            "source_id": "trace:validation/003",
            "source_type": "verified_runtime",
            "locator": "trace:validation/003",
            "claim": "A failed validation exposes retry and review paths.",
            "grade": "verified_runtime",
            "observed_at": "2026-09-21T00:00:00Z",
            "notes": "",
        },
        {
            "evidence_id": "trace_review_state",
            "source_id": "trace:validation/004",
            "source_type": "verified_runtime",
            "locator": "trace:validation/004",
            "claim": "Human review creates awaiting_human_review.",
            "grade": "verified_runtime",
            "observed_at": "2026-09-21T00:00:00Z",
            "notes": "",
        },
        {
            "evidence_id": "binding_retry_call",
            "source_id": "validation_client.py",
            "source_type": "code",
            "locator": "validation_client.py:retry",
            "claim": "validation_client.retry(record_id) was observed starting a validation run.",
            "grade": "verified_runtime",
            "observed_at": "2026-09-21T00:00:00Z",
            "notes": "binding evidence: invocation observed",
        },
        {
            "evidence_id": "binding_review_cli",
            "source_id": "docs/validation.md#review-cli",
            "source_type": "sop",
            "locator": "docs/validation.md#review-cli",
            "claim": "The runbook says `review-cli open --record <id>` opens a review ticket.",
            "grade": "documented",
            "observed_at": None,
            "notes": "binding evidence: documented only, never observed running",
        },
    ]
    return {
        "surface_candidates.yaml": {
            "schema_version": "1.0",
            "discovery_mode": "illustrative",
            "source_root": "docs/validation.md",
            "review_required": True,
            "surface_candidates": [
                {
                    "candidate_id": "validation_failure_next_action",
                    "input_description": "Validation failure context and retry budget.",
                    "observed_options": ["retry", "human_review"],
                    "bounded_output": True,
                    "semantic_interpretation_required": True,
                    "current_implementation": "workflow_branch",
                    "replacement_strength": "strong",
                    "source_locator": "docs/validation.md#failure",
                    "review_status": "promoted",
                }
            ],
        },
        "action_registry.yaml": {
            "schema_version": "1.0",
            "system_id": "validation_example",
            "actions": [
                {
                    "action_id": "retry",
                    "description": "Retry the failed validation.",
                    "aliases": ["run validation again"],
                    "choose_when": "The failure appears transient and retry budget remains.",
                    "do_not_choose_when": "The failure is semantic, deterministic, or retry budget is exhausted.",
                    "preconditions": ["retry_budget > 0", "record_exists == true"],
                    "parameters": [{"name": "record_id", "type": "string", "required": True}],
                    "outputs": ["validation_result"],
                    "side_effects": ["starts_validation_run"],
                    "risk": "low",
                    "reversible": True,
                    "requires_confirmation": False,
                    "allowed_from_states": ["validation_failed"],
                    "destination_states": ["validation_pending"],
                    "success_condition": "validation_run_started",
                    "evidence_refs": ["runbook_retry", "trace_retry_01", "trace_failed_01"],
                    "examples": ["Transient timeout"],
                    "counterexamples": ["Invalid schema"],
                    "binding": {
                        "kind": "python_callable",
                        "locator": "validation_client:retry",
                        "arg_mapping": {"record_id": "record_id"},
                        "evidence_refs": ["binding_retry_call"],
                    },
                },
                {
                    "action_id": "human_review",
                    "description": "Pause automation and request a human decision.",
                    "aliases": ["manual review", "escalate"],
                    "choose_when": "The failure is ambiguous or retry is not safe.",
                    "do_not_choose_when": "A deterministic retry policy resolves the failure.",
                    "preconditions": ["unresolved_failure == true"],
                    "parameters": [
                        {"name": "record_id", "type": "string", "required": True},
                        {"name": "evidence", "type": "object", "required": False},
                    ],
                    "outputs": ["review_ticket_id"],
                    "side_effects": ["pauses_automation", "creates_review_ticket"],
                    "risk": "low",
                    "reversible": True,
                    "requires_confirmation": False,
                    "allowed_from_states": ["validation_failed"],
                    "destination_states": ["awaiting_human_review"],
                    "success_condition": "review_ticket_created",
                    "evidence_refs": ["runbook_review", "trace_review_01", "trace_review_state"],
                    "examples": ["Ambiguous schema error"],
                    "counterexamples": ["Transient timeout with budget"],
                    "binding": {
                        "kind": "cli",
                        "locator": "review-cli",
                        "argv": ["review-cli", "open", "--record", "{record_id}"],
                        "arg_mapping": {"record_id": "record_id"},
                        "evidence_refs": ["binding_review_cli"],
                    },
                },
            ],
        },
        "state_registry.yaml": {
            "schema_version": "1.0",
            "system_id": "validation_example",
            "states": [
                {
                    "state_id": "validation_pending",
                    "description": "A validation run is in progress.",
                    "observable_predicate": "validation_status == 'pending'",
                    "entry_evidence_refs": ["trace_pending_01"],
                    "terminal": False,
                },
                {
                    "state_id": "validation_failed",
                    "description": "The latest validation run failed.",
                    "observable_predicate": "validation_status == 'failed'",
                    "entry_evidence_refs": ["trace_failed_01"],
                    "terminal": False,
                },
                {
                    "state_id": "awaiting_human_review",
                    "description": "Automation is paused and a review ticket is open.",
                    "observable_predicate": "review_ticket_open == true",
                    "entry_evidence_refs": ["trace_review_state"],
                    "terminal": False,
                },
            ],
        },
        "transition_graph.yaml": {
            "schema_version": "1.0",
            "system_id": "validation_example",
            "transitions": [
                {
                    "transition_id": "retry_failed_validation",
                    "source_state": "validation_failed",
                    "action_id": "retry",
                    "destination_state": "validation_pending",
                    "guard": ["retry_budget > 0", "record_exists == true"],
                    "outcome": "validation_run_started",
                    "evidence_refs": ["trace_retry_01"],
                },
                {
                    "transition_id": "review_failed_validation",
                    "source_state": "validation_failed",
                    "action_id": "human_review",
                    "destination_state": "awaiting_human_review",
                    "guard": ["unresolved_failure == true"],
                    "outcome": "review_ticket_created",
                    "evidence_refs": ["trace_review_01"],
                },
            ],
        },
        "decision_surfaces.yaml": {
            "schema_version": "1.0",
            "system_id": "validation_example",
            "decision_surfaces": [
                {
                    "surface_id": "resolve_validation_failure",
                    "description": "Choose the next step after a validation failure.",
                    "activation": {"state_id": "validation_failed"},
                    "candidate_actions": [
                        {
                            "action_id": "retry",
                            "criterion": "Failure appears transient and retry budget remains.",
                            "evidence_refs": ["runbook_retry", "trace_retry_01"],
                        },
                        {
                            "action_id": "human_review",
                            "criterion": "Failure is ambiguous or retry is unsafe.",
                            "evidence_refs": ["runbook_review", "trace_review_01"],
                        },
                    ],
                    "fallback_action": "human_review",
                    "abstention_choice": "human_review",
                    "production": True,
                    "evidence_refs": ["runbook_retry", "runbook_review", "trace_failed_01"],
                }
            ],
        },
        "evidence_ledger.jsonl": evidence,
        "jev_adapter_spec.yaml": {
            "schema_version": "1.0",
            "system_id": "validation_example",
            "provider": "vendor_neutral",
            "policy": {"min_confidence": 0.85},
            "classifier_questions": [
                {
                    "question_id": "next_validation_action",
                    "surface_id": "resolve_validation_failure",
                    "type": "choice",
                    "instruction": "Select the action whose criterion best matches the evidence.",
                    "choices": [
                        {
                            "id": "retry",
                            "criterion": "Failure appears transient and retry budget remains.",
                            "executor_action_id": "retry",
                        },
                        {
                            "id": "human_review",
                            "criterion": "Failure is ambiguous or retry is unsafe.",
                            "executor_action_id": "human_review",
                        },
                    ],
                    "abstention_choice": "human_review",
                }
            ],
        },
        "coverage_report.md": (
            "# Coverage report\n\n"
            "Example bundle generated by init_action_bundle.py.\n\n"
            "## Inspected sources\n- SOP\n- Runtime traces\n\n"
            "## Known blind spots\n- No live permission system was inspected.\n"
        ),
    }


def empty_bundle() -> dict[str, Any]:
    return {
        "surface_candidates.yaml": {
            "schema_version": "1.0",
            "discovery_mode": "unconfigured",
            "source_root": None,
            "review_required": True,
            "surface_candidates": [],
        },
        "action_registry.yaml": {"schema_version": "1.0", "system_id": "unconfigured_system", "actions": []},
        "state_registry.yaml": {"schema_version": "1.0", "system_id": "unconfigured_system", "states": []},
        "transition_graph.yaml": {"schema_version": "1.0", "system_id": "unconfigured_system", "transitions": []},
        "decision_surfaces.yaml": {"schema_version": "1.0", "system_id": "unconfigured_system", "decision_surfaces": []},
        "evidence_ledger.jsonl": [],
        "jev_adapter_spec.yaml": {"schema_version": "1.0", "system_id": "unconfigured_system", "provider": "vendor_neutral", "classifier_questions": []},
        "coverage_report.md": "# Coverage report\n\nNo sources inspected yet.\n",
    }


def write_bundle(path: Path, bundle: dict[str, Any], force: bool) -> None:
    path.mkdir(parents=True, exist_ok=True)
    existing = [path / name for name in FILES if (path / name).exists()]
    if existing and not force:
        names = ", ".join(p.name for p in existing)
        raise SystemExit(f"Refusing to overwrite existing files: {names}. Use --force.")
    for name, value in bundle.items():
        target = path / name
        if name.endswith(".yaml"):
            target.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
        elif name.endswith(".jsonl"):
            target.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in value), encoding="utf-8")
        else:
            target.write_text(value, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--example", action="store_true", help="write a small validated example")
    parser.add_argument("--force", action="store_true", help="overwrite bundle files")
    args = parser.parse_args()
    write_bundle(args.path, example_bundle() if args.example else empty_bundle(), args.force)
    print(f"created {args.path} ({'example' if args.example else 'empty'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
