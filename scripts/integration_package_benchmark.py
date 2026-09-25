#!/usr/bin/env python3
"""Run the pinned public Work System -> Integration Package benchmark."""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import forge  # noqa: E402
import integration_package  # noqa: E402


def main() -> int:
    manifest = ROOT / "tests" / "fixtures" / "work-system-benchmark" / "manifest.yaml"
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "integration-project"
        forge.reconstruct_project(
            project=project,
            evidence_manifest=manifest,
            system_id="openai_agents_contribution_work",
        )
        result = forge.prepare_integration(project)
        errors = integration_package.verify_package(project)
        work_map = yaml.safe_load((project / "work_system_map.yaml").read_text(encoding="utf-8"))
        source_hash = hashlib.sha256((project / "work_system_map.yaml").read_bytes()).hexdigest()
        registry = yaml.safe_load(
            (project / "integration" / "action_registry.yaml").read_text(encoding="utf-8")
        )
        policy = yaml.safe_load(
            (project / "integration" / "legal_action_policy.yaml").read_text(encoding="utf-8")
        )
        passed = (
            result["stage"] == "integration_planned"
            and not errors
            and result["source"]["sha256"] == source_hash
            and result["source"]["source_inventory"] == work_map["source_inventory"]
            and all(not action["binding"]["executable"] for action in registry["actions"])
            and all(surface["fallback_action_id"] is None for surface in policy["surfaces"])
            and not result["safety"]["executes_actions"]
        )
        if not passed:
            print(f"INTEGRATION PACKAGE BENCHMARK: HOLD ({errors})")
            return 1
        print(
            f"openai_agents_contribution_work: decisions={len(result['selected_decisions'])} "
            f"actions={len(registry['actions'])} provenance=True executable_bindings=0"
        )
        print("INTEGRATION PACKAGE BENCHMARK: PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
