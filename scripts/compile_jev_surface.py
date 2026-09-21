#!/usr/bin/env python3
"""Compile a deterministic, JEV-ready choice set for one observable state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


PRODUCTION_GRADES = {"verified_runtime", "verified_schema", "documented", "observed_trace"}


def load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: top-level value must be a mapping")
    return value


def evidence_grades(bundle: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (bundle / "evidence_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            result[item["evidence_id"]] = item.get("grade", "")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--state", required=True, help="observable state_id")
    parser.add_argument("--surface", required=True, help="decision surface_id")
    args = parser.parse_args()

    action_doc = load(args.bundle / "action_registry.yaml")
    state_doc = load(args.bundle / "state_registry.yaml")
    surface_doc = load(args.bundle / "decision_surfaces.yaml")
    grades = evidence_grades(args.bundle)

    states = {item["state_id"]: item for item in state_doc.get("states", [])}
    actions = {item["action_id"]: item for item in action_doc.get("actions", [])}
    surfaces = {item["surface_id"]: item for item in surface_doc.get("decision_surfaces", [])}
    if args.state not in states:
        raise SystemExit(f"unknown state: {args.state}")
    if args.surface not in surfaces:
        raise SystemExit(f"unknown surface: {args.surface}")

    surface = surfaces[args.surface]
    activation = surface.get("activation", {})
    if activation.get("state_id") != args.state:
        raise SystemExit(
            f"surface {args.surface} is activated by {activation.get('state_id')!r}, not {args.state!r}"
        )
    if not surface.get("production", False):
        raise SystemExit("refusing to compile a non-production surface; mark production: true after review")

    choices = []
    for candidate in surface.get("candidate_actions", []):
        action_id = candidate.get("action_id")
        if action_id not in actions:
            raise SystemExit(f"surface references unknown action: {action_id}")
        action = actions[action_id]
        if args.state not in action.get("allowed_from_states", []):
            raise SystemExit(f"action {action_id} is illegal from state {args.state}")
        refs = action.get("evidence_refs", [])
        if not any(grades.get(ref) in PRODUCTION_GRADES for ref in refs):
            raise SystemExit(f"action {action_id} has no production-grade evidence")
        choices.append(
            {
                "id": action_id,
                "executor_action_id": action_id,
                "description": action.get("description"),
                "criterion": candidate.get("criterion"),
                "risk": action.get("risk"),
                "requires_confirmation": bool(action.get("requires_confirmation", False)),
                "parameters": action.get("parameters", []),
                "destination_states": action.get("destination_states", []),
            }
        )

    output = {
        "schema_version": "1.0",
        "surface_id": args.surface,
        "state_id": args.state,
        "choices": choices,
        "fallback_action": surface.get("fallback_action"),
        "abstention_choice": surface.get("abstention_choice"),
        "execution_boundary": "JEV selects one id; deterministic adapter checks preconditions again before execution.",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
