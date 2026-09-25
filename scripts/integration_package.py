#!/usr/bin/env python3
"""Compile a Work System Map into a non-executing integration contract.

The output tells an implementation agent exactly what must be integrated and
verified. It does not infer a safe fallback, executable binding, observable
state mapping, or permission from reconstruction evidence.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml


ARTIFACT_NAMES = (
    "action_registry",
    "observable_state_adapter",
    "legal_action_policy",
    "controller_plan",
    "loop_plan",
    "verification_contract",
    "telemetry_contract",
)
OBSERVATION_INPUT_FIELDS = {"observation_id", "occurred_at", "current_state"}
OBSERVATION_OUTPUT_FIELDS = {"state_id", "available_action_ids"}
CONTROLLER_STEPS = [
    "observe",
    "validate_state",
    "filter_legal_actions",
    "request_bounded_judgment",
    "apply_confidence_policy",
    "emit_decision_record",
]
LOOP_STOP_CONDITIONS = {
    "terminal_state",
    "adapter_error",
    "no_legal_action",
    "low_confidence",
    "human_review",
}
SHADOW_STOP_CONDITIONS = {
    "terminal_state",
    "unknown_state",
    "ambiguous_state",
    "no_legal_action",
    "low_confidence",
    "human_review",
    "adapter_error",
    "decider_error",
    "illegal_answer",
    "unchanged_state",
    "max_iterations",
    "observation_exhausted",
}
TELEMETRY_FIELDS = {
    "event_id",
    "occurred_at",
    "observation_id",
    "state_id",
    "decision_id",
    "candidate_action_ids",
    "selected_action_id",
    "confidence",
    "policy_outcome",
    "binding_status",
    "error",
}
VERIFICATION_CHECKS = {
    "observation_shape",
    "state_is_known",
    "legal_action_subset",
    "binding_coverage",
    "stop_conditions",
    "telemetry_shape",
}
BINDING_KINDS = {"python_callable", "http", "cli", "mcp", "ui"}


class IntegrationPackageError(ValueError):
    """A work-system map cannot safely produce an integration contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "item"


def _stable(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{_slug(value)}_{digest}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise IntegrationPackageError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise IntegrationPackageError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrationPackageError(f"{path} must contain a mapping")
    return value


def _dump(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _decision_candidates(work_map: Mapping[str, Any], decision_id: str | None) -> list[dict[str, Any]]:
    candidates = [dict(item) for item in work_map.get("decision_candidates", []) if isinstance(item, Mapping)]
    if decision_id:
        candidates = [item for item in candidates if item.get("node_id") == decision_id]
        if not candidates:
            raise IntegrationPackageError(f"bounded decision {decision_id!r} was not found")
    if not candidates:
        raise IntegrationPackageError("work-system map contains no bounded decision candidates")
    return candidates


def _verification_checks() -> list[dict[str, Any]]:
    descriptions = {
        "observation_shape": "Adapter emits every required observation field.",
        "state_is_known": "Every emitted state resolves to a reviewed state ID.",
        "legal_action_subset": "Available actions are a subset of the surface registry.",
        "binding_coverage": "Each executable action has a verified binding and postcondition.",
        "stop_conditions": "The host enforces every required stop condition and a finite iteration limit.",
        "telemetry_shape": "Every decision record contains the required audit fields.",
    }
    return [
        {"check_id": check_id, "status": "pending", "description": descriptions[check_id]}
        for check_id in sorted(VERIFICATION_CHECKS)
    ]


def compile_package(
    *,
    project: str | Path,
    decision_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(project).expanduser().resolve()
    project_file = root / "forge_project.yaml"
    map_file = root / "work_system_map.yaml"
    if not project_file.is_file() or not map_file.is_file():
        raise IntegrationPackageError("integration preparation requires a work_system_mapped project")
    manifest = _load(project_file)
    if manifest.get("stage") != "work_system_mapped":
        raise IntegrationPackageError(
            f"integration preparation requires stage 'work_system_mapped', got {manifest.get('stage')!r}"
        )
    package = root / "integration"
    if package.exists() and any(package.iterdir()) and not force:
        raise IntegrationPackageError(f"integration directory is not empty: {package}; use --force to refresh")
    package.mkdir(parents=True, exist_ok=True)

    work_map = _load(map_file)
    if work_map.get("schema_version") != "1.0" or work_map.get("mode") != "work_system_reconstruction":
        raise IntegrationPackageError("integration preparation requires a validated Work System Map")
    decisions = _decision_candidates(work_map, decision_id)
    nodes = {
        item.get("node_id"): item
        for item in work_map.get("nodes", [])
        if isinstance(item, Mapping) and isinstance(item.get("node_id"), str)
    }

    selected: list[dict[str, Any]] = []
    actions_by_id: dict[str, dict[str, Any]] = {}
    surfaces: list[dict[str, Any]] = []
    for decision in decisions:
        node_id = str(decision.get("node_id", ""))
        actions = decision.get("candidate_actions")
        if node_id not in nodes or nodes[node_id].get("kind") != "decision":
            raise IntegrationPackageError(f"decision candidate {node_id!r} has no decision node")
        if (
            not isinstance(actions, list)
            or not 2 <= len(actions) <= 12
            or len(set(actions)) != len(actions)
            or not all(isinstance(action, str) and action for action in actions)
        ):
            raise IntegrationPackageError(f"decision {node_id}: bounded actions are required")
        action_ids: list[str] = []
        for action_name in actions:
            action_id = _stable("action", f"{node_id}:{action_name}")
            action_ids.append(action_id)
            actions_by_id[action_id] = {
                "action_id": action_id,
                "label": action_name,
                "decision_id": node_id,
                "legal": {
                    "status": "unverified",
                    "allowed_state_ids": [],
                    "preconditions": [],
                    "blocking_reason": "Work-system reconstruction does not prove runtime legality.",
                },
                "binding": {
                    "status": "stub",
                    "executable": False,
                    "authority": "none",
                    "kind": "unverified",
                    "locator": None,
                    "verification_required": [
                        "identify the target operation",
                        "verify deterministic preconditions",
                        "verify observable success and failure postconditions",
                        "capture verified_runtime or observed_trace evidence",
                    ],
                },
            }
        surface_id = _stable("surface", node_id)
        surfaces.append(
            {
                "surface_id": surface_id,
                "decision_id": node_id,
                "candidate_action_ids": action_ids,
                "activation": {
                    "status": "unverified",
                    "state_ids": [],
                    "blocking_reason": "No reviewed runtime state mapping exists.",
                },
                "fallback_action_id": None,
                "fallback_status": "missing_reviewed_safe_fallback",
                "default_policy_outcome": "abstain",
            }
        )
        selected.append({"decision_id": node_id, "surface_id": surface_id, "action_ids": action_ids})

    states = [
        {
            "state_id": _stable("state", str(item["node_id"])),
            "source_node_id": item["node_id"],
            "label": item.get("label") or item["node_id"],
            "mapping_status": "unverified",
            "observation_predicate": None,
        }
        for item in nodes.values()
        if item.get("kind") == "state"
    ]

    system_id = work_map.get("system_id")
    artifacts: dict[str, dict[str, Any]] = {
        "action_registry": {
            "schema_version": "1.0",
            "system_id": system_id,
            "actions": list(actions_by_id.values()),
            "authority": "descriptive_only",
        },
        "observable_state_adapter": {
            "schema_version": "1.0",
            "system_id": system_id,
            "status": "implementation_required",
            "states": states,
            "input": {"format": "mapping", "required_fields": sorted(OBSERVATION_INPUT_FIELDS)},
            "output": {"required_fields": sorted(OBSERVATION_OUTPUT_FIELDS)},
            "unknown_state_policy": "stop_and_review",
            "executes_actions": False,
        },
        "legal_action_policy": {
            "schema_version": "1.0",
            "system_id": system_id,
            "status": "blocked_until_state_and_preconditions_are_reviewed",
            "surfaces": surfaces,
            "rules": [
                "reject unknown state IDs",
                "reject actions absent from the selected surface",
                "reject actions whose legality is unverified",
                "abstain when no reviewed safe fallback exists",
            ],
            "executes_actions": False,
        },
        "controller_plan": {
            "schema_version": "1.0",
            "system_id": system_id,
            "status": "implementation_required",
            "steps": CONTROLLER_STEPS,
            "jev": {
                "role": "choose only among action IDs already proven legal by code",
                "confidence_required": True,
            },
            "action_execution": "prohibited",
        },
        "loop_plan": {
            "schema_version": "1.0",
            "system_id": system_id,
            "status": "blocked_until_adapter_policy_and_bindings_are_verified",
            "cycle": ["observe", "decide", "record"],
            "stop_conditions": sorted(LOOP_STOP_CONDITIONS),
            "max_iterations": None,
            "blocking_requirements": [
                "set a positive finite max_iterations",
                "implement and verify every stop condition",
                "verify adapter state mappings",
                "verify at least one safe fallback",
                "verify bindings before adding execution to the cycle",
            ],
            "executes_actions": False,
        },
        "verification_contract": {
            "schema_version": "1.0",
            "system_id": system_id,
            "checks": _verification_checks(),
            "target_execution": "prohibited",
            "promotion_requires": [
                "verified_runtime or observed_trace binding evidence",
                "reviewed state and legality policy",
                "representative integration tests",
                "calibrated JEV shadow evidence",
                "named human release decision",
            ],
        },
        "telemetry_contract": {
            "schema_version": "1.0",
            "system_id": system_id,
            "required_fields": sorted(TELEMETRY_FIELDS),
            "privacy": {"capture_chain_of_thought": False, "capture_secrets": False},
            "destination": "host_application_owned_log",
            "executes_actions": False,
        },
    }
    for name, value in artifacts.items():
        _dump(package / f"{name}.yaml", value)

    artifact_index = {
        name: {
            "file": f"integration/{name}.yaml",
            "sha256": _sha256(package / f"{name}.yaml"),
        }
        for name in ARTIFACT_NAMES
    }
    integration_manifest = {
        "schema_version": "1.0",
        "system_id": system_id,
        "stage": "integration_planned",
        "source": {
            "stage": "work_system_mapped",
            "file": "work_system_map.yaml",
            "sha256": _sha256(map_file),
            "source_inventory": work_map.get("source_inventory", []),
        },
        "selected_decisions": selected,
        "artifacts": artifact_index,
        "safety": {
            "descriptive_only": True,
            "executes_actions": False,
            "production_authority": False,
            "bindings_executable": False,
            "shadow_eligible": False,
        },
        "blocking_gates": [
            "implement_observation_adapter",
            "review_state_and_legal_action_policy",
            "verify_safe_fallback",
            "verify_runtime_bindings",
            "implement_bounded_loop",
            "collect_telemetry_and_trusted_labels",
            "pass_shadow_release_gate",
        ],
        "next_action": "implement_and_verify_integration_contracts",
    }
    _dump(package / "integration_manifest.yaml", integration_manifest)
    manifest["stage"] = "integration_planned"
    manifest["updated_at"] = _now()
    manifest["next_action"] = "implement_and_verify_integration_contracts"
    manifest.setdefault("artifacts", {})["integration_package"] = "integration/integration_manifest.yaml"
    manifest.setdefault("safety", {}).update(
        {"executes_actions": False, "production_authority": False, "shadow_eligible": False}
    )
    _dump(project_file, manifest)
    return integration_manifest


def _as_string_set(value: Any) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        return set()
    return set(value)


def verify_package(project: str | Path) -> list[str]:
    """Validate package relationships without importing or calling the target system."""
    project_root = Path(project).expanduser().resolve()
    package = project_root / "integration"
    errors: list[str] = []
    try:
        manifest = _load(package / "integration_manifest.yaml")
    except IntegrationPackageError as exc:
        return [str(exc)]
    system_id = manifest.get("system_id")
    if manifest.get("stage") != "integration_planned":
        errors.append("integration manifest stage is invalid")
    safety = manifest.get("safety")
    controller_shadow_ready = isinstance(safety, Mapping) and safety.get("controller_shadow_ready") is True
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in ("executes_actions", "production_authority", "bindings_executable", "shadow_eligible")
    ):
        errors.append("integration manifest safety boundary is invalid")

    source = manifest.get("source")
    map_file = project_root / "work_system_map.yaml"
    if not isinstance(source, Mapping) or source.get("stage") != "work_system_mapped":
        errors.append("work-system source provenance is missing")
    elif not map_file.is_file() or source.get("sha256") != _sha256(map_file):
        errors.append("work-system source hash mismatch")

    documents: dict[str, dict[str, Any]] = {}
    artifact_index = manifest.get("artifacts")
    if not isinstance(artifact_index, Mapping):
        errors.append("artifact index is missing")
        artifact_index = {}
    for name in ARTIFACT_NAMES:
        entry = artifact_index.get(name)
        path = package / f"{name}.yaml"
        if not isinstance(entry, Mapping) or entry.get("file") != f"integration/{name}.yaml":
            errors.append(f"artifact index is invalid for {name}")
            continue
        if not path.is_file():
            errors.append(f"missing {name}")
            continue
        if entry.get("sha256") != _sha256(path):
            errors.append(f"artifact hash mismatch for {name}")
        try:
            document = _load(path)
        except IntegrationPackageError as exc:
            errors.append(str(exc))
            continue
        if document.get("system_id") != system_id or document.get("schema_version") != "1.0":
            errors.append(f"identity mismatch for {name}")
        documents[name] = document

    registry = documents.get("action_registry", {})
    actions = registry.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append("action registry is empty")
        actions = []
    action_ids: list[str] = []
    for action in actions:
        if not isinstance(action, Mapping) or not isinstance(action.get("action_id"), str):
            errors.append("action registry contains an invalid action")
            continue
        action_id = str(action["action_id"])
        action_ids.append(action_id)
        binding = action.get("binding")
        legal = action.get("legal")
        binding_is_safe = False
        if isinstance(binding, Mapping):
            common_safe = binding.get("executable") is False and binding.get("authority") == "none"
            if binding.get("status") == "stub":
                binding_is_safe = common_safe and binding.get("locator") is None
            elif binding.get("status") == "verified_for_shadow":
                binding_is_safe = (
                    common_safe
                    and binding.get("production_authority") is False
                    and binding.get("kind") in BINDING_KINDS
                    and isinstance(binding.get("locator"), Mapping)
                    and binding.get("evidence_grade") == "observed_trace"
                    and isinstance(binding.get("binding_id"), str)
                    and isinstance(binding.get("binding_digest"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", str(binding.get("binding_digest"))) is not None
                    and isinstance(binding.get("observation_source_sha256"), str)
                    and re.fullmatch(r"[0-9a-f]{64}", str(binding.get("observation_source_sha256"))) is not None
                    and binding.get("verification_ref") == "integration/binding_verification.yaml"
                )
        if not binding_is_safe:
            errors.append(f"action {action_id} has an unsafe binding")
        if controller_shadow_ready:
            if (
                not isinstance(legal, Mapping)
                or legal.get("status") != "reviewed_for_shadow"
                or legal.get("authorizes_execution") is not False
                or not isinstance(legal.get("allowed_state_ids"), list)
                or not isinstance(legal.get("preconditions"), list)
            ):
                errors.append(f"action {action_id} has invalid shadow legality")
        elif not isinstance(legal, Mapping) or legal.get("status") != "unverified":
            errors.append(f"action {action_id} claims unverified legality")
    if len(action_ids) != len(set(action_ids)):
        errors.append("action IDs are not unique")

    adapter = documents.get("observable_state_adapter", {})
    if _as_string_set((adapter.get("input") or {}).get("required_fields")) != OBSERVATION_INPUT_FIELDS:
        errors.append("observation adapter input contract is invalid")
    if _as_string_set((adapter.get("output") or {}).get("required_fields")) != OBSERVATION_OUTPUT_FIELDS:
        errors.append("observation adapter output contract is invalid")
    if adapter.get("executes_actions") is not False or adapter.get("unknown_state_policy") != "stop_and_review":
        errors.append("observation adapter safety policy is invalid")
    if controller_shadow_ready:
        states = adapter.get("states")
        if (
            adapter.get("status") != "verified_for_shadow"
            or not isinstance(states, list)
            or len(states) < 2
            or not any(isinstance(item, Mapping) and item.get("terminal") is True for item in states)
            or any(
                not isinstance(item, Mapping)
                or item.get("mapping_status") != "reviewed_for_shadow"
                or not isinstance(item.get("observation_predicate"), str)
                for item in states
            )
        ):
            errors.append("observation adapter shadow contract is invalid")

    policy = documents.get("legal_action_policy", {})
    surfaces = policy.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        errors.append("legal-action policy has no surfaces")
        surfaces = []
    selected = {
        item.get("surface_id"): set(item.get("action_ids") or [])
        for item in manifest.get("selected_decisions", [])
        if isinstance(item, Mapping)
    }
    for surface in surfaces:
        if not isinstance(surface, Mapping):
            errors.append("legal-action policy contains an invalid surface")
            continue
        surface_id = surface.get("surface_id")
        candidates = _as_string_set(surface.get("candidate_action_ids"))
        if not candidates or not candidates <= set(action_ids) or candidates != selected.get(surface_id):
            errors.append(f"surface {surface_id} has invalid action coverage")
        activation = surface.get("activation")
        if controller_shadow_ready:
            if (
                not isinstance(activation, Mapping)
                or activation.get("status") != "reviewed_for_shadow"
                or not _as_string_set(activation.get("state_ids"))
            ):
                errors.append(f"surface {surface_id} has invalid shadow activation")
            if (
                surface.get("fallback_action_id") not in candidates
                or surface.get("fallback_status") != "reviewed_for_shadow_abstention"
                or surface.get("default_policy_outcome") != "abstain"
            ):
                errors.append(f"surface {surface_id} has invalid shadow fallback")
        else:
            if not isinstance(activation, Mapping) or activation.get("status") != "unverified" or activation.get("state_ids") != []:
                errors.append(f"surface {surface_id} invents an activation mapping")
            if surface.get("fallback_action_id") is not None or surface.get("fallback_status") != "missing_reviewed_safe_fallback":
                errors.append(f"surface {surface_id} invents a safe fallback")
    if policy.get("executes_actions") is not False:
        errors.append("legal-action policy may execute actions")

    controller = documents.get("controller_plan", {})
    if controller.get("steps") != CONTROLLER_STEPS or controller.get("action_execution") != "prohibited":
        errors.append("controller plan is invalid")
    if controller_shadow_ready and (
        controller.get("status") != "shadow_ready"
        or controller.get("invokes_bindings") is not False
        or not isinstance(controller.get("question"), str)
    ):
        errors.append("controller shadow plan is invalid")
    loop = documents.get("loop_plan", {})
    if controller_shadow_ready:
        maximum = loop.get("max_iterations")
        if (
            _as_string_set(loop.get("stop_conditions")) != SHADOW_STOP_CONDITIONS
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= 100
            or loop.get("executes_actions") is not False
            or loop.get("status") != "shadow_ready"
        ):
            errors.append("shadow loop plan does not fail closed")
    elif (
        _as_string_set(loop.get("stop_conditions")) != LOOP_STOP_CONDITIONS
        or loop.get("max_iterations") is not None
        or loop.get("executes_actions") is not False
        or not str(loop.get("status", "")).startswith("blocked_")
    ):
        errors.append("loop plan does not fail closed")
    verification = documents.get("verification_contract", {})
    checks = verification.get("checks")
    check_ids = {
        item.get("check_id")
        for item in checks or []
        if isinstance(item, Mapping) and item.get("status") == "pending"
    }
    if check_ids != VERIFICATION_CHECKS or verification.get("target_execution") != "prohibited":
        errors.append("verification contract is invalid")
    telemetry = documents.get("telemetry_contract", {})
    privacy = telemetry.get("privacy")
    if (
        _as_string_set(telemetry.get("required_fields")) != TELEMETRY_FIELDS
        or not isinstance(privacy, Mapping)
        or privacy.get("capture_chain_of_thought") is not False
        or privacy.get("capture_secrets") is not False
        or telemetry.get("executes_actions") is not False
    ):
        errors.append("telemetry contract is invalid")
    return errors


__all__ = ["IntegrationPackageError", "compile_package", "verify_package"]
