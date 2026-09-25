#!/usr/bin/env python3
"""Compile and run a finite, non-executing shadow controller.

The controller adapts observable state, filters legal actions with deterministic
predicates, and gives only those action IDs to a host-supplied decider. It never
imports or invokes an action binding. Its records contain state fingerprints,
not raw observations or private reasoning.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import yaml

import binding_verification
import integration_package
from predicates import PredicateError, evaluate_all, evaluate_predicate, parse_predicate


REQUIRED_STOP_CONDITIONS = {
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
FORBIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "private_reasoning",
    "reasoning_trace",
    "scratchpad",
}
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
SECRET_PATTERN = re.compile(r"(?i)(bearer\s|api[_-]?key|password|secret|access[_-]?token|authorization)")


class ShadowControllerError(ValueError):
    """The controller proposal or persisted contract is invalid."""


class IllegalShadowChoice(ShadowControllerError):
    """A decider returned an action outside the deterministic legal set."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ShadowControllerError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _mapping_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ShadowControllerError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShadowControllerError(f"{path} must contain a mapping")
    return value


def _write_yaml(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def _identifier(value: Any, *, owner: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ShadowControllerError(f"{owner} must be a stable identifier")
    return value


def _predicates(value: Any, *, owner: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "possibly empty" if allow_empty else "non-empty"
        raise ShadowControllerError(f"{owner} must be a {qualifier} predicate list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ShadowControllerError(f"{owner}: every predicate must be a non-empty string")
        try:
            parse_predicate(item)
        except PredicateError as exc:
            raise ShadowControllerError(f"{owner}: invalid predicate {item!r}: {exc}") from exc
        result.append(item.strip())
    return result


def _validate_evidence(value: Any, *, sources: Mapping[str, Mapping[str, Any]], owner: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"source_id", "source_sha256", "quote"}:
        raise ShadowControllerError(f"{owner}: evidence requires source_id, source_sha256, and quote")
    source_id = value.get("source_id")
    source = sources.get(source_id) if isinstance(source_id, str) else None
    if source is None:
        raise ShadowControllerError(f"{owner}: unknown evidence source {source_id!r}")
    if value.get("source_sha256") != source.get("sha256"):
        raise ShadowControllerError(f"{owner}: source_sha256 does not match inventoried evidence")
    quote = value.get("quote")
    if not isinstance(quote, str) or not quote.strip() or quote not in str(source.get("text", "")):
        raise ShadowControllerError(f"{owner}: quote is not present in source {source_id!r}")
    return {"source_id": source_id, "source_sha256": str(source["sha256"]), "quote": quote}


def _contains_private_reasoning(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            if normalized in FORBIDDEN_REASONING_KEYS or _contains_private_reasoning(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_private_reasoning(item) for item in value)
    return False


def _contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(SECRET_PATTERN.search(str(key)) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and SECRET_PATTERN.search(value) is not None


def _fingerprint(value: Mapping[str, Any]) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ShadowControllerError("observation must be JSON-serializable") from exc
    return hashlib.sha256(payload).hexdigest()


def prepare_controller(*, project: str | Path, proposal: str | Path) -> dict[str, Any]:
    """Compile a reviewed state/legal-action policy into a shadow-only plan."""
    root = Path(project).expanduser().resolve()
    project_file = root / "forge_project.yaml"
    project_manifest = _load_yaml(project_file)
    if project_manifest.get("stage") != "bindings_verified_for_shadow":
        raise ShadowControllerError(
            "controller preparation requires stage 'bindings_verified_for_shadow', "
            f"got {project_manifest.get('stage')!r}"
        )
    binding_errors = binding_verification.verify_binding_state(root)
    package_errors = integration_package.verify_package(root)
    if binding_errors or package_errors:
        raise ShadowControllerError((binding_errors + package_errors)[0])
    proposal_path = Path(proposal).expanduser().resolve()
    document = _load_yaml(proposal_path)
    allowed_top = {"schema_version", "system_id", "states", "surface", "loop"}
    extra_top = sorted(set(document) - allowed_top)
    if extra_top:
        raise ShadowControllerError(f"controller proposal has unsupported fields: {', '.join(extra_top)}")
    if document.get("schema_version") != "1.0" or document.get("system_id") != project_manifest.get("system_id"):
        raise ShadowControllerError("controller proposal identity does not match the Forge project")
    if _contains_secret(document):
        raise ShadowControllerError("controller proposal contains a secret or credential")
    sources = binding_verification.resolve_evidence_sources(root, project_manifest)

    raw_states = document.get("states")
    if not isinstance(raw_states, list) or len(raw_states) < 2:
        raise ShadowControllerError("controller proposal requires at least one active and one terminal state")
    states: list[dict[str, Any]] = []
    state_ids: set[str] = set()
    for index, raw in enumerate(raw_states, 1):
        if not isinstance(raw, Mapping):
            raise ShadowControllerError(f"state {index} must be a mapping")
        extra = sorted(set(raw) - {"state_id", "label", "predicate", "terminal", "evidence"})
        if extra:
            raise ShadowControllerError(f"state {index}: unsupported fields: {', '.join(extra)}")
        state_id = _identifier(raw.get("state_id"), owner=f"state {index} state_id")
        if state_id in state_ids:
            raise ShadowControllerError(f"duplicate state_id {state_id!r}")
        label = raw.get("label")
        predicate = raw.get("predicate")
        if not isinstance(label, str) or not label.strip():
            raise ShadowControllerError(f"state {state_id}: label is required")
        if not isinstance(predicate, str) or not predicate.strip():
            raise ShadowControllerError(f"state {state_id}: predicate is required")
        try:
            parse_predicate(predicate)
        except PredicateError as exc:
            raise ShadowControllerError(f"state {state_id}: invalid predicate: {exc}") from exc
        if not isinstance(raw.get("terminal"), bool):
            raise ShadowControllerError(f"state {state_id}: terminal must be boolean")
        states.append(
            {
                "state_id": state_id,
                "label": label.strip(),
                "predicate": predicate.strip(),
                "terminal": raw["terminal"],
                "evidence": _validate_evidence(raw.get("evidence"), sources=sources, owner=f"state {state_id}"),
            }
        )
        state_ids.add(state_id)
    terminal_ids = {item["state_id"] for item in states if item["terminal"]}
    nonterminal_ids = state_ids - terminal_ids
    if not terminal_ids or not nonterminal_ids:
        raise ShadowControllerError("controller needs both terminal and non-terminal observable states")

    package = root / "integration"
    registry_path = package / "action_registry.yaml"
    registry = _load_yaml(registry_path)
    known_actions = {
        item.get("action_id"): item
        for item in registry.get("actions", [])
        if isinstance(item, Mapping) and isinstance(item.get("action_id"), str)
    }
    existing_policy_path = package / "legal_action_policy.yaml"
    existing_policy = _load_yaml(existing_policy_path)
    known_surfaces = {
        item.get("surface_id"): item
        for item in existing_policy.get("surfaces", [])
        if isinstance(item, Mapping) and isinstance(item.get("surface_id"), str)
    }
    raw_surface = document.get("surface")
    if not isinstance(raw_surface, Mapping):
        raise ShadowControllerError("controller proposal requires one surface mapping")
    surface_fields = {
        "surface_id",
        "question",
        "question_id",
        "active_state_ids",
        "fallback_action_id",
        "min_confidence",
        "actions",
    }
    extra_surface = sorted(set(raw_surface) - surface_fields)
    if extra_surface:
        raise ShadowControllerError(f"surface has unsupported fields: {', '.join(extra_surface)}")
    surface_id = raw_surface.get("surface_id")
    if surface_id not in known_surfaces:
        raise ShadowControllerError(f"unknown surface_id {surface_id!r}")
    expected_action_ids = set(known_surfaces[surface_id].get("candidate_action_ids") or [])
    question = raw_surface.get("question")
    question_id = raw_surface.get("question_id")
    _identifier(question_id, owner="surface question_id")
    if not isinstance(question, str) or not question.strip():
        raise ShadowControllerError("surface question is required")
    active_state_ids = raw_surface.get("active_state_ids")
    if (
        not isinstance(active_state_ids, list)
        or not active_state_ids
        or len(set(active_state_ids)) != len(active_state_ids)
        or not set(active_state_ids) <= nonterminal_ids
    ):
        raise ShadowControllerError("surface active_state_ids must be unique known non-terminal states")
    threshold = raw_surface.get("min_confidence")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < float(threshold) <= 1:
        raise ShadowControllerError("surface min_confidence must be greater than 0 and at most 1")
    raw_actions = raw_surface.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ShadowControllerError("surface actions must be a non-empty list")
    actions: list[dict[str, Any]] = []
    seen_actions: set[str] = set()
    for index, raw in enumerate(raw_actions, 1):
        if not isinstance(raw, Mapping):
            raise ShadowControllerError(f"surface action {index} must be a mapping")
        extra = sorted(set(raw) - {"action_id", "allowed_state_ids", "preconditions", "evidence"})
        if extra:
            raise ShadowControllerError(f"surface action {index}: unsupported fields: {', '.join(extra)}")
        action_id = raw.get("action_id")
        if action_id not in known_actions or action_id not in expected_action_ids:
            raise ShadowControllerError(f"surface action {index}: unknown action {action_id!r}")
        if action_id in seen_actions:
            raise ShadowControllerError(f"duplicate surface action {action_id!r}")
        allowed = raw.get("allowed_state_ids")
        if (
            not isinstance(allowed, list)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or not set(allowed) <= set(active_state_ids)
        ):
            raise ShadowControllerError(f"action {action_id}: allowed_state_ids must be active states")
        actions.append(
            {
                "action_id": action_id,
                "label": known_actions[action_id].get("label"),
                "allowed_state_ids": list(allowed),
                "preconditions": _predicates(
                    raw.get("preconditions"), owner=f"action {action_id} preconditions", allow_empty=True
                ),
                "evidence": _validate_evidence(
                    raw.get("evidence"), sources=sources, owner=f"action {action_id}"
                ),
            }
        )
        seen_actions.add(str(action_id))
    if seen_actions != expected_action_ids:
        missing = sorted(expected_action_ids - seen_actions)
        raise ShadowControllerError(f"surface action coverage is incomplete: {missing}")
    fallback = raw_surface.get("fallback_action_id")
    fallback_rule = next((item for item in actions if item["action_id"] == fallback), None)
    if fallback_rule is None:
        raise ShadowControllerError("surface fallback_action_id must be a known candidate action")
    if set(fallback_rule["allowed_state_ids"]) != set(active_state_ids) or fallback_rule["preconditions"]:
        raise ShadowControllerError("fallback must be unconditional and legal in every active state")

    raw_loop = document.get("loop")
    if not isinstance(raw_loop, Mapping) or set(raw_loop) != {"max_iterations", "stop_conditions"}:
        raise ShadowControllerError("loop requires only max_iterations and stop_conditions")
    max_iterations = raw_loop.get("max_iterations")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 1 <= max_iterations <= 100:
        raise ShadowControllerError("loop max_iterations must be an integer between 1 and 100")
    stop_conditions = raw_loop.get("stop_conditions")
    if not isinstance(stop_conditions, list) or set(stop_conditions) != REQUIRED_STOP_CONDITIONS:
        raise ShadowControllerError("loop stop_conditions must contain the complete fail-closed set")

    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "system_id": project_manifest["system_id"],
        "stage": "shadow_controller_ready",
        "states": states,
        "surface": {
            "surface_id": surface_id,
            "question_id": question_id,
            "question": question.strip(),
            "active_state_ids": list(active_state_ids),
            "candidate_action_ids": [item["action_id"] for item in actions],
            "fallback_action_id": fallback,
            "min_confidence": float(threshold),
            "threshold_status": "provisional_for_shadow",
            "actions": actions,
        },
        "loop": {
            "max_iterations": max_iterations,
            "stop_conditions": sorted(REQUIRED_STOP_CONDITIONS),
        },
        "safety": {
            "calls_jev_during_compilation": False,
            "invokes_bindings": False,
            "executes_actions": False,
            "production_authority": False,
        },
    }
    plan["plan_digest"] = _mapping_digest(plan)

    plan_path = package / "shadow_controller_plan.yaml"
    controller_manifest_path = package / "shadow_controller_manifest.yaml"
    if plan_path.exists() or controller_manifest_path.exists():
        raise ShadowControllerError("shadow controller artifacts already exist")
    _write_yaml(plan_path, plan)

    state_adapter_path = package / "observable_state_adapter.yaml"
    state_adapter = _load_yaml(state_adapter_path)
    state_adapter.update(
        {
            "status": "verified_for_shadow",
            "states": [
                {
                    "state_id": item["state_id"],
                    "label": item["label"],
                    "mapping_status": "reviewed_for_shadow",
                    "observation_predicate": item["predicate"],
                    "terminal": item["terminal"],
                    "evidence": item["evidence"],
                }
                for item in states
            ],
            "unknown_state_policy": "stop_and_review",
            "executes_actions": False,
        }
    )
    _write_yaml(state_adapter_path, state_adapter)

    action_rules = {item["action_id"]: item for item in actions}
    for action in registry.get("actions", []):
        if not isinstance(action, dict) or action.get("action_id") not in action_rules:
            continue
        rule = action_rules[action["action_id"]]
        action["legal"] = {
            "status": "reviewed_for_shadow",
            "allowed_state_ids": rule["allowed_state_ids"],
            "preconditions": rule["preconditions"],
            "evidence": rule["evidence"],
            "authorizes_execution": False,
        }
    _write_yaml(registry_path, registry)

    legal_policy = {
        "schema_version": "1.0",
        "system_id": project_manifest["system_id"],
        "status": "verified_for_shadow",
        "surfaces": [
            {
                "surface_id": surface_id,
                "decision_id": known_surfaces[surface_id].get("decision_id"),
                "candidate_action_ids": [item["action_id"] for item in actions],
                "activation": {"status": "reviewed_for_shadow", "state_ids": list(active_state_ids)},
                "fallback_action_id": fallback,
                "fallback_status": "reviewed_for_shadow_abstention",
                "default_policy_outcome": "abstain",
                "min_confidence": float(threshold),
            }
        ],
        "rules": [
            "reject unknown or ambiguous states",
            "offer only actions whose reviewed predicates pass",
            "reject answers outside the legal set",
            "route low confidence to the reviewed fallback and stop",
        ],
        "executes_actions": False,
    }
    _write_yaml(existing_policy_path, legal_policy)

    controller_plan_path = package / "controller_plan.yaml"
    controller_plan = _load_yaml(controller_plan_path)
    controller_plan.update(
        {
            "status": "shadow_ready",
            "question": question.strip(),
            "question_id": question_id,
            "min_confidence": float(threshold),
            "threshold_status": "provisional_for_shadow",
            "steps": integration_package.CONTROLLER_STEPS,
            "action_execution": "prohibited",
            "invokes_bindings": False,
        }
    )
    _write_yaml(controller_plan_path, controller_plan)

    loop_plan_path = package / "loop_plan.yaml"
    loop_plan = _load_yaml(loop_plan_path)
    loop_plan.update(
        {
            "status": "shadow_ready",
            "cycle": ["observe", "validate_state", "filter_legal_actions", "decide", "record"],
            "stop_conditions": sorted(REQUIRED_STOP_CONDITIONS),
            "max_iterations": max_iterations,
            "blocking_requirements": [
                "collect trusted shadow labels and calibration evidence",
                "keep bindings non-executable until a separate release gate",
                "obtain named production release approval",
            ],
            "executes_actions": False,
        }
    )
    _write_yaml(loop_plan_path, loop_plan)

    integration_manifest_path = package / "integration_manifest.yaml"
    integration_manifest = _load_yaml(integration_manifest_path)
    for name in integration_package.ARTIFACT_NAMES:
        path = package / f"{name}.yaml"
        integration_manifest["artifacts"][name]["sha256"] = _sha256(path)
    integration_manifest["safety"]["controller_shadow_ready"] = True
    integration_manifest["next_action"] = "run_shadow_controller_and_collect_trusted_labels"
    _write_yaml(integration_manifest_path, integration_manifest)

    tracked = {
        "shadow_controller_plan": plan_path,
        "action_registry": registry_path,
        "observable_state_adapter": state_adapter_path,
        "legal_action_policy": existing_policy_path,
        "controller_plan": controller_plan_path,
        "loop_plan": loop_plan_path,
        "integration_manifest": integration_manifest_path,
    }
    manifest = {
        "schema_version": "1.0",
        "system_id": project_manifest["system_id"],
        "stage": "shadow_controller_ready",
        "created_at": _now(),
        "proposal_source": {"locator": str(proposal_path), "sha256": _sha256(proposal_path)},
        "artifacts": {
            name: {"file": path.relative_to(root).as_posix(), "sha256": _sha256(path)}
            for name, path in tracked.items()
        },
        "safety": dict(plan["safety"]),
        "next_action": "run_shadow_controller_and_collect_trusted_labels",
    }
    _write_yaml(controller_manifest_path, manifest)
    project_manifest["stage"] = "shadow_controller_ready"
    project_manifest["updated_at"] = _now()
    project_manifest["next_action"] = manifest["next_action"]
    project_manifest.setdefault("artifacts", {})["shadow_controller_manifest"] = (
        "integration/shadow_controller_manifest.yaml"
    )
    project_manifest.setdefault("safety", {}).update(
        {
            "executes_actions": False,
            "production_authority": False,
            "shadow_eligible": False,
            "shadow_controller_ready": True,
        }
    )
    _write_yaml(project_file, project_manifest)
    errors = verify_controller(root)
    if errors:
        raise ShadowControllerError(errors[0])
    return manifest


def verify_controller(project: str | Path) -> list[str]:
    """Verify persisted controller artifacts and cross-contract invariants."""
    root = Path(project).expanduser().resolve()
    package = root / "integration"
    errors: list[str] = []
    try:
        manifest = _load_yaml(package / "shadow_controller_manifest.yaml")
    except ShadowControllerError as exc:
        return [str(exc)]
    if manifest.get("stage") != "shadow_controller_ready":
        errors.append("shadow controller manifest stage is invalid")
    try:
        project_manifest = _load_yaml(root / "forge_project.yaml")
        if project_manifest.get("stage") != "shadow_controller_ready":
            errors.append("Forge project stage does not match the shadow controller")
    except ShadowControllerError as exc:
        errors.append(str(exc))
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in ("calls_jev_during_compilation", "invokes_bindings", "executes_actions", "production_authority")
    ):
        errors.append("shadow controller manifest safety boundary is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return errors + ["shadow controller artifact index is missing"]
    for name, entry in artifacts.items():
        if not isinstance(entry, Mapping) or not isinstance(entry.get("file"), str):
            errors.append(f"shadow controller artifact index is invalid for {name}")
            continue
        path = root / entry["file"]
        if not path.is_file():
            errors.append(f"missing shadow controller artifact {name}")
        elif _sha256(path) != entry.get("sha256"):
            label = "shadow controller plan" if name == "shadow_controller_plan" else f"artifact {name}"
            errors.append(f"{label} hash mismatch")
    if errors:
        return errors
    try:
        plan = _load_yaml(package / "shadow_controller_plan.yaml")
        ShadowController(plan)
    except ShadowControllerError as exc:
        errors.append(str(exc))
    errors.extend(binding_verification.verify_binding_state(root))
    errors.extend(integration_package.verify_package(root))
    return errors


Decider = Callable[[str, Mapping[str, Any], list[str]], Mapping[str, Any]]


class ShadowController:
    """Finite controller that records choices but cannot execute an action."""

    def __init__(self, plan: Mapping[str, Any]) -> None:
        self.plan = dict(plan)
        safety = self.plan.get("safety")
        if not isinstance(safety, Mapping) or any(
            safety.get(field) is not False
            for field in ("calls_jev_during_compilation", "invokes_bindings", "executes_actions", "production_authority")
        ):
            raise ShadowControllerError("shadow controller safety boundary is invalid")
        if self.plan.get("stage") != "shadow_controller_ready":
            raise ShadowControllerError("shadow controller plan stage is invalid")
        digest = self.plan.get("plan_digest")
        expected = _mapping_digest({key: value for key, value in self.plan.items() if key != "plan_digest"})
        if digest != expected:
            raise ShadowControllerError("shadow controller plan digest mismatch")
        loop = self.plan.get("loop") or {}
        if set(loop.get("stop_conditions") or []) != REQUIRED_STOP_CONDITIONS:
            raise ShadowControllerError("shadow controller stop conditions are incomplete")
        max_iterations = loop.get("max_iterations")
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 1 <= max_iterations <= 100:
            raise ShadowControllerError("shadow controller max_iterations is invalid")
        self.binding_invocations = 0

    def _state(self, observation: Mapping[str, Any]) -> tuple[str, str | None]:
        matches = [
            item
            for item in self.plan.get("states", [])
            if isinstance(item, Mapping) and evaluate_predicate(str(item.get("predicate", "")), observation)
        ]
        if not matches:
            return "unknown_state", None
        if len(matches) > 1:
            return "ambiguous_state", None
        return ("terminal_state" if matches[0].get("terminal") else "active", str(matches[0]["state_id"]))

    def _legal_actions(self, state_id: str, observation: Mapping[str, Any]) -> list[str]:
        result: list[str] = []
        surface = self.plan["surface"]
        by_id = {
            item["action_id"]: item
            for item in surface.get("actions", [])
            if isinstance(item, Mapping) and isinstance(item.get("action_id"), str)
        }
        for action_id in surface.get("candidate_action_ids", []):
            rule = by_id.get(action_id)
            if (
                rule is not None
                and state_id in rule.get("allowed_state_ids", [])
                and not evaluate_all(rule.get("preconditions", []), observation)
            ):
                result.append(action_id)
        return result

    def _record(
        self,
        *,
        iteration: int,
        observation_sha256: str | None,
        state_id: str | None,
        legal_actions: list[str],
        proposed: str | None = None,
        selected: str | None = None,
        confidence: float | None = None,
        threshold: float | None = None,
        abstained: bool = False,
        policy_outcome: str,
        stop_reason: str | None = None,
        error_type: str | None = None,
        decision_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "iteration": iteration,
            "question_id": self.plan["surface"]["question_id"],
            "controller_digest": self.plan["plan_digest"],
            "observation_sha256": observation_sha256,
            "state_id": state_id,
            "legal_action_ids": list(legal_actions),
            "proposed_action_id": proposed,
            "selected_action_id": selected,
            "confidence": confidence,
            "threshold": threshold,
            "abstained": abstained,
            "policy_outcome": policy_outcome,
            "stop_reason": stop_reason,
            "error_type": error_type,
            "binding_invoked": False,
        }
        if decision_metadata:
            record.update(decision_metadata)
        return record

    @staticmethod
    def _decision_metadata(answer: Mapping[str, Any], legal_actions: list[str]) -> dict[str, Any]:
        """Keep only bounded operational metadata returned by a decider."""
        metadata: dict[str, Any] = {}
        model = answer.get("model")
        if isinstance(model, str) and model and len(model) <= 128:
            metadata["model"] = model
        provider = answer.get("provider")
        if isinstance(provider, str) and provider and len(provider) <= 64:
            metadata["provider"] = provider
        latency = answer.get("latency_ms")
        if (
            isinstance(latency, (int, float))
            and not isinstance(latency, bool)
            and math.isfinite(float(latency))
            and latency >= 0
        ):
            metadata["latency_ms"] = round(float(latency), 3)
        probabilities = answer.get("probabilities")
        if isinstance(probabilities, Mapping) and set(probabilities) <= set(legal_actions):
            safe_probabilities: dict[str, float] = {}
            for key, value in probabilities.items():
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or not 0 <= float(value) <= 1
                ):
                    safe_probabilities = {}
                    break
                safe_probabilities[str(key)] = float(value)
            if safe_probabilities:
                metadata["probabilities"] = safe_probabilities
        usage = answer.get("usage")
        if isinstance(usage, Mapping):
            safe_usage = {
                str(key): value
                for key, value in usage.items()
                if isinstance(key, str)
                and len(key) <= 64
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and value >= 0
            }
            if len(safe_usage) == len(usage):
                metadata["usage"] = safe_usage
        return metadata

    def run(self, observations: Iterable[Mapping[str, Any]], decider: Decider) -> dict[str, Any]:
        """Evaluate observable states with a host decider and execute nothing."""
        self.binding_invocations = 0
        records: list[dict[str, Any]] = []
        seen_observations: set[str] = set()
        max_iterations = int(self.plan["loop"]["max_iterations"])
        for iteration, observation in enumerate(observations, 1):
            if iteration > max_iterations:
                return {
                    "records": records,
                    "stop_reason": "max_iterations",
                    "iterations": len(records),
                    "binding_invocations": self.binding_invocations,
                }
            if (
                not isinstance(observation, Mapping)
                or _contains_private_reasoning(observation)
                or _contains_secret(observation)
            ):
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=None,
                        state_id=None,
                        legal_actions=[],
                        policy_outcome="stop",
                        stop_reason="adapter_error",
                        error_type="private_or_invalid_observation",
                    )
                )
                return {
                    "records": records,
                    "stop_reason": "adapter_error",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            try:
                observation_hash = _fingerprint(observation)
                state_status, state_id = self._state(observation)
            except Exception as exc:
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=None,
                        state_id=None,
                        legal_actions=[],
                        policy_outcome="stop",
                        stop_reason="adapter_error",
                        error_type=type(exc).__name__,
                    )
                )
                return {
                    "records": records,
                    "stop_reason": "adapter_error",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            if observation_hash in seen_observations:
                record = self._record(
                    iteration=iteration,
                    observation_sha256=observation_hash,
                    state_id=state_id,
                    legal_actions=[],
                    policy_outcome="stop",
                    stop_reason="unchanged_state",
                )
                records.append(record)
                return {
                    "records": records,
                    "stop_reason": "unchanged_state",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            seen_observations.add(observation_hash)
            if state_status != "active":
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=observation_hash,
                        state_id=state_id,
                        legal_actions=[],
                        policy_outcome="stop",
                        stop_reason=state_status,
                    )
                )
                return {
                    "records": records,
                    "stop_reason": state_status,
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            assert state_id is not None
            legal = self._legal_actions(state_id, observation)
            if not legal:
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=observation_hash,
                        state_id=state_id,
                        legal_actions=[],
                        policy_outcome="stop",
                        stop_reason="no_legal_action",
                    )
                )
                return {
                    "records": records,
                    "stop_reason": "no_legal_action",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            surface = self.plan["surface"]
            try:
                answer = decider(str(surface["question"]), observation, list(legal))
            except Exception as exc:
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=observation_hash,
                        state_id=state_id,
                        legal_actions=legal,
                        policy_outcome="stop",
                        stop_reason="decider_error",
                        error_type=type(exc).__name__,
                    )
                )
                return {
                    "records": records,
                    "stop_reason": "decider_error",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            if not isinstance(answer, Mapping):
                raise ShadowControllerError("decider response must be a mapping with answer and confidence")
            proposed = answer.get("answer")
            confidence = answer.get("confidence")
            if proposed not in legal:
                raise IllegalShadowChoice(f"decider returned illegal action {proposed!r}")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
                raise ShadowControllerError("decider confidence must be between 0 and 1")
            threshold = float(surface["min_confidence"])
            fallback = str(surface["fallback_action_id"])
            decision_metadata = self._decision_metadata(answer, legal)
            if float(confidence) < threshold:
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=observation_hash,
                        state_id=state_id,
                        legal_actions=legal,
                        proposed=str(proposed),
                        selected=fallback,
                        confidence=float(confidence),
                        threshold=threshold,
                        abstained=True,
                        policy_outcome="fallback",
                        stop_reason="low_confidence",
                        decision_metadata=decision_metadata,
                    )
                )
                return {
                    "records": records,
                    "stop_reason": "low_confidence",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            if proposed == fallback:
                records.append(
                    self._record(
                        iteration=iteration,
                        observation_sha256=observation_hash,
                        state_id=state_id,
                        legal_actions=legal,
                        proposed=str(proposed),
                        selected=fallback,
                        confidence=float(confidence),
                        threshold=threshold,
                        abstained=True,
                        policy_outcome="human_review",
                        stop_reason="human_review",
                        decision_metadata=decision_metadata,
                    )
                )
                return {
                    "records": records,
                    "stop_reason": "human_review",
                    "iterations": len(records),
                    "binding_invocations": 0,
                }
            records.append(
                self._record(
                    iteration=iteration,
                    observation_sha256=observation_hash,
                    state_id=state_id,
                    legal_actions=legal,
                    proposed=str(proposed),
                    selected=str(proposed),
                    confidence=float(confidence),
                    threshold=threshold,
                    policy_outcome="shadow_selected",
                    decision_metadata=decision_metadata,
                )
            )
        return {
            "records": records,
            "stop_reason": "observation_exhausted",
            "iterations": len(records),
            "binding_invocations": self.binding_invocations,
        }


def load_runtime(project: str | Path) -> ShadowController:
    root = Path(project).expanduser().resolve()
    errors = verify_controller(root)
    if errors:
        raise ShadowControllerError(errors[0])
    return ShadowController(_load_yaml(root / "integration" / "shadow_controller_plan.yaml"))


__all__ = [
    "IllegalShadowChoice",
    "ShadowController",
    "ShadowControllerError",
    "load_runtime",
    "prepare_controller",
    "verify_controller",
]
