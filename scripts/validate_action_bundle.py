#!/usr/bin/env python3
"""Validate references and safety invariants in an Action Bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicates import PredicateError, parse_predicate  # noqa: E402


EVIDENCE_GRADES = {
    "verified_runtime",
    "verified_schema",
    "documented",
    "observed_trace",
    "inferred",
    "hypothetical",
}
PRODUCTION_GRADES = EVIDENCE_GRADES - {"inferred", "hypothetical"}
RISKS = {"low", "medium", "high", "critical"}
QUESTION_TYPES = {"choice", "noul", "score"}
CRITERIA_SOURCES = {"static", "dynamic"}
BINDING_KINDS = {"python_callable", "http", "cli", "mcp", "ui"}
BINDING_EXECUTABLE_GRADES = {"verified_runtime", "observed_trace"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
UI_OPERATIONS = {"goto", "click", "fill", "select", "press", "check", "uncheck", "read"}
SECRET_PATTERN = re.compile(r"(?i)(bearer\s|api[_-]?key|password|secret|token)")
PYTHON_LOCATOR = re.compile(r"^[\w.]+:[\w.]+$")
REQUIRED_FILES = (
    "surface_candidates.yaml",
    "action_registry.yaml",
    "state_registry.yaml",
    "transition_graph.yaml",
    "decision_surfaces.yaml",
    "evidence_ledger.jsonl",
    "jev_adapter_spec.yaml",
)


def load_yaml(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{path.name}: invalid YAML: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name}: top-level value must be a mapping")
        return {}
    return value


def items(data: dict[str, Any], key: str, path: str, errors: list[str]) -> list[dict[str, Any]]:
    value = data.get(key, [])
    if not isinstance(value, list):
        errors.append(f"{path}: {key} must be a list")
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{path}.{key}[{index}]: must be a mapping")
        else:
            result.append(item)
    return result


def unique_ids(values: list[dict[str, Any]], field: str, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        identifier = value.get(field)
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{label}[{index}]: missing {field}")
            continue
        if identifier in result:
            errors.append(f"{label}: duplicate {field} {identifier!r}")
        result[identifier] = value
    return result


def refs(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [ref for ref in value if isinstance(ref, str)]


def validate(bundle: Path) -> tuple[list[str], list[str], dict[str, int]]:
    """Return (errors, warnings, counts) for the bundle directory."""
    errors: list[str] = []
    warnings: list[str] = []

    missing = [name for name in REQUIRED_FILES if not (bundle / name).is_file()]
    for name in missing:
        errors.append(f"missing required file: {name}")
    if missing:
        return errors, warnings, {}

    actions_doc = load_yaml(bundle / "action_registry.yaml", errors)
    candidates_doc = load_yaml(bundle / "surface_candidates.yaml", errors)
    states_doc = load_yaml(bundle / "state_registry.yaml", errors)
    graph_doc = load_yaml(bundle / "transition_graph.yaml", errors)
    surfaces_doc = load_yaml(bundle / "decision_surfaces.yaml", errors)
    adapter_doc = load_yaml(bundle / "jev_adapter_spec.yaml", errors)

    actions = unique_ids(items(actions_doc, "actions", "action_registry.yaml", errors), "action_id", "actions", errors)
    candidates = unique_ids(
        items(candidates_doc, "surface_candidates", "surface_candidates.yaml", errors),
        "candidate_id",
        "surface_candidates",
        errors,
    )
    states = unique_ids(items(states_doc, "states", "state_registry.yaml", errors), "state_id", "states", errors)
    transitions = unique_ids(items(graph_doc, "transitions", "transition_graph.yaml", errors), "transition_id", "transitions", errors)
    surfaces = unique_ids(items(surfaces_doc, "decision_surfaces", "decision_surfaces.yaml", errors), "surface_id", "decision_surfaces", errors)
    questions = unique_ids(items(adapter_doc, "classifier_questions", "jev_adapter_spec.yaml", errors), "question_id", "classifier_questions", errors)

    for candidate_id, candidate in candidates.items():
        for field in (
            "input_description",
            "observed_options",
            "bounded_output",
            "semantic_interpretation_required",
            "current_implementation",
            "replacement_strength",
            "source_locator",
            "review_status",
        ):
            if field not in candidate:
                errors.append(f"surface candidate {candidate_id}: missing {field}")
        if not isinstance(candidate.get("observed_options"), list):
            errors.append(f"surface candidate {candidate_id}: observed_options must be a list")
        if candidate.get("replacement_strength") not in {"strong", "medium", "weak", "none"}:
            errors.append(f"surface candidate {candidate_id}: invalid replacement_strength")

    evidence: dict[str, dict[str, Any]] = {}
    ledger_path = bundle / "evidence_ledger.jsonl"
    for line_number, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"evidence_ledger.jsonl:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(item, dict):
            errors.append(f"evidence_ledger.jsonl:{line_number}: entry must be an object")
            continue
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            errors.append(f"evidence_ledger.jsonl:{line_number}: missing evidence_id")
            continue
        if evidence_id in evidence:
            errors.append(f"evidence_ledger.jsonl: duplicate evidence_id {evidence_id!r}")
        evidence[evidence_id] = item
        for required in ("source_id", "source_type", "locator", "claim", "grade"):
            if required not in item:
                errors.append(f"evidence {evidence_id}: missing {required}")
        if item.get("grade") not in EVIDENCE_GRADES:
            errors.append(f"evidence {evidence_id}: invalid grade {item.get('grade')!r}")

    def check_evidence(ref_list: Any, owner: str, production: bool = False) -> None:
        if not isinstance(ref_list, list):
            errors.append(f"{owner}: evidence_refs must be a list")
            return
        for ref in ref_list:
            if ref not in evidence:
                errors.append(f"{owner}: unknown evidence reference {ref!r}")
        if production and ref_list and not any(evidence.get(ref, {}).get("grade") in PRODUCTION_GRADES for ref in ref_list):
            errors.append(f"{owner}: production surface has no production-grade evidence")

    def check_predicates(values: Any, owner: str) -> None:
        texts = [values] if isinstance(values, str) else (values if isinstance(values, list) else [])
        for text in texts:
            if not isinstance(text, str):
                errors.append(f"{owner}: predicate must be a string")
                continue
            try:
                parse_predicate(text)
            except PredicateError as exc:
                errors.append(f"{owner}: unparseable predicate {text!r} ({exc})")

    def looks_secret(value: Any, path: str) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "auth_env":
                    continue
                found.extend(looks_secret(item, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found.extend(looks_secret(item, f"{path}[{index}]"))
        elif isinstance(value, str) and SECRET_PATTERN.search(value):
            found.append(path)
        return found

    def check_binding(action_id: str, action: dict[str, Any]) -> None:
        binding = action.get("binding")
        if binding is None:
            warnings.append(f"action {action_id}: no binding; handler will be generated as a stub")
            return
        owner = f"action {action_id} binding"
        if not isinstance(binding, dict):
            errors.append(f"{owner}: must be a mapping")
            return
        kind = binding.get("kind")
        if kind not in BINDING_KINDS:
            errors.append(f"{owner}: invalid kind {kind!r}")
        locator = binding.get("locator")
        if not isinstance(locator, str) or not locator:
            errors.append(f"{owner}: locator must be a non-empty string")
        binding_refs = binding.get("evidence_refs")
        if not isinstance(binding_refs, list) or not binding_refs:
            errors.append(f"{owner}: binding without evidence")
        else:
            check_evidence(binding_refs, owner)
            if not any(evidence.get(ref, {}).get("grade") in BINDING_EXECUTABLE_GRADES for ref in binding_refs):
                warnings.append(f"{owner}: no verified_runtime/observed_trace evidence; handler will be generated as a stub")
        if kind == "http":
            if str(binding.get("method", "POST")).upper() not in HTTP_METHODS:
                errors.append(f"{owner}: invalid http method {binding.get('method')!r}")
            if isinstance(locator, str) and not locator.startswith("https://"):
                errors.append(f"{owner}: http locator must be an https:// URL template")
        elif kind == "cli":
            argv = binding.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
                errors.append(f"{owner}: cli binding needs a non-empty argv list of strings")
        elif kind == "python_callable":
            if isinstance(locator, str) and not PYTHON_LOCATOR.match(locator):
                errors.append(f"{owner}: python_callable locator must look like module:callable")
        elif kind == "mcp":
            for field in ("server", "tool"):
                if not binding.get(field):
                    errors.append(f"{owner}: mcp binding needs {field}")
            transport = binding.get("transport", "stdio")
            if transport not in {"stdio", "http"}:
                errors.append(f"{owner}: mcp transport must be stdio or http")
            elif transport == "stdio" and not binding.get("command"):
                errors.append(f"{owner}: stdio mcp binding needs command")
            elif transport == "http" and not (isinstance(binding.get("url"), str) and binding["url"].startswith("https://")):
                errors.append(f"{owner}: http mcp binding needs an https:// url")
        elif kind == "ui":
            operation = binding.get("operation", "click")
            if operation not in UI_OPERATIONS:
                errors.append(f"{owner}: ui operation must be one of {sorted(UI_OPERATIONS)}")
            if binding.get("driver", "playwright") != "playwright":
                errors.append(f"{owner}: ui driver must be playwright")
            url = binding.get("url")
            if url is not None and not (isinstance(url, str) and url.startswith(("https://", "http://localhost", "http://127.0.0.1"))):
                errors.append(f"{owner}: ui url must be https:// (or localhost)")
            if operation in {"fill", "select", "press"}:
                mapping = binding.get("arg_mapping") or {}
                if "value" not in mapping and not any(isinstance(p, dict) and p.get("name") == "value" for p in action.get("parameters", [])):
                    errors.append(f"{owner}: ui operation {operation} needs a 'value' parameter or arg_mapping entry")
        mapping = binding.get("arg_mapping")
        if mapping is not None:
            if not isinstance(mapping, dict):
                errors.append(f"{owner}: arg_mapping must be a mapping")
            else:
                parameters = {p.get("name") for p in action.get("parameters", []) if isinstance(p, dict)}
                for name in mapping:
                    if name not in parameters:
                        errors.append(f"{owner}: arg_mapping key {name!r} is not a declared parameter")
                for parameter in action.get("parameters", []):
                    if isinstance(parameter, dict) and parameter.get("required") and parameter.get("name") not in mapping:
                        warnings.append(f"{owner}: required parameter {parameter.get('name')!r} has no arg_mapping entry")
        for path in looks_secret(binding, owner):
            errors.append(f"{path}: value looks like a secret; use auth_env and read it from the environment")

    for action_id, action in actions.items():
        required = (
            "description", "choose_when", "do_not_choose_when", "preconditions",
            "parameters", "outputs", "side_effects", "risk", "reversible",
            "allowed_from_states", "destination_states", "success_condition", "evidence_refs",
        )
        for field in required:
            if field not in action:
                errors.append(f"action {action_id}: missing {field}")
        risk = action.get("risk")
        if risk not in RISKS:
            errors.append(f"action {action_id}: invalid risk {risk!r}")
        allowed_states = action.get("allowed_from_states", [])
        destination_states = action.get("destination_states", [])
        if not isinstance(allowed_states, list) or not allowed_states:
            errors.append(f"action {action_id}: allowed_from_states must be non-empty")
        if not isinstance(destination_states, list) or not destination_states:
            errors.append(f"action {action_id}: destination_states must be non-empty")
        for state_id in refs(allowed_states) + refs(destination_states):
            if state_id not in states:
                errors.append(f"action {action_id}: unknown state {state_id!r}")
        action_refs = action.get("evidence_refs", [])
        check_evidence(action_refs, f"action {action_id}")
        if risk in {"high", "critical"} and not action.get("preconditions"):
            errors.append(f"action {action_id}: high-risk action requires preconditions")
        if action.get("reversible") is False and not (action.get("requires_confirmation") or action.get("policy_gate")):
            errors.append(f"action {action_id}: irreversible action requires requires_confirmation or policy_gate")
        check_predicates(action.get("preconditions", []), f"action {action_id} preconditions")
        check_binding(action_id, action)

    for state_id, state in states.items():
        if not state.get("observable_predicate") and not state.get("runtime_field"):
            errors.append(f"state {state_id}: needs observable_predicate or runtime_field")
        if state.get("observable_predicate"):
            check_predicates(state.get("observable_predicate"), f"state {state_id} observable_predicate")
        check_evidence(state.get("entry_evidence_refs", []), f"state {state_id}")

    for transition_id, transition in transitions.items():
        source = transition.get("source_state")
        action_id = transition.get("action_id")
        destination = transition.get("destination_state")
        if source not in states:
            errors.append(f"transition {transition_id}: unknown source_state {source!r}")
        if destination not in states:
            errors.append(f"transition {transition_id}: unknown destination_state {destination!r}")
        if action_id not in actions:
            errors.append(f"transition {transition_id}: unknown action_id {action_id!r}")
        elif source in states and source not in actions[action_id].get("allowed_from_states", []):
            errors.append(f"transition {transition_id}: action {action_id!r} is not allowed from {source!r}")
        check_evidence(transition.get("evidence_refs", []), f"transition {transition_id}")
        check_predicates(transition.get("guard", []), f"transition {transition_id} guard")

    for surface_id, surface in surfaces.items():
        activation = surface.get("activation", {})
        state_id = activation.get("state_id") if isinstance(activation, dict) else None
        if state_id not in states:
            errors.append(f"surface {surface_id}: activation.state_id must reference a known state")
        production = bool(surface.get("production", False))
        surface_candidates = items(surface, "candidate_actions", f"surface {surface_id}", errors)
        candidate_ids: list[str] = []
        for index, candidate in enumerate(surface_candidates):
            action_id = candidate.get("action_id")
            candidate_ids.append(action_id)
            if action_id not in actions:
                errors.append(f"surface {surface_id}.candidate_actions[{index}]: unknown action {action_id!r}")
                continue
            if state_id in states and state_id not in actions[action_id].get("allowed_from_states", []):
                errors.append(f"surface {surface_id}: action {action_id!r} is illegal from {state_id!r}")
            if not candidate.get("criterion"):
                errors.append(f"surface {surface_id}: candidate {action_id!r} missing criterion")
            check_evidence(candidate.get("evidence_refs", []), f"surface {surface_id} candidate {action_id}", production)
            if production:
                action_evidence = actions[action_id].get("evidence_refs", [])
                if not any(evidence.get(ref, {}).get("grade") in PRODUCTION_GRADES for ref in action_evidence):
                    errors.append(f"surface {surface_id}: action {action_id!r} lacks production-grade action evidence")
        if len(set(candidate_ids)) != len(candidate_ids):
            errors.append(f"surface {surface_id}: duplicate candidate action")
        if len(candidate_ids) < 2:
            warnings.append(f"surface {surface_id}: fewer than two candidate actions")
        for fallback_field in ("fallback_action", "abstention_choice"):
            value = surface.get(fallback_field)
            if value not in candidate_ids:
                errors.append(f"surface {surface_id}: {fallback_field} must be one of candidate actions")
            elif value in actions and (actions[value].get("reversible") is False or actions[value].get("risk") == "critical"):
                errors.append(f"surface {surface_id}: {fallback_field} {value!r} must be reversible and not critical")
        check_evidence(surface.get("evidence_refs", []), f"surface {surface_id}", production)

    if "dynamic_criteria_source" in adapter_doc:
        warnings.append("jev_adapter_spec: dynamic_criteria_source is deprecated; set criteria_source: dynamic on the question")
    endpoint = adapter_doc.get("endpoint")
    if endpoint is not None and (not isinstance(endpoint, str) or not endpoint.startswith("https://")):
        errors.append("jev_adapter_spec: endpoint must be an https:// URL")
    model = adapter_doc.get("model")
    if model is not None and not isinstance(model, str):
        errors.append("jev_adapter_spec: model must be a string")

    question_executors: dict[str, set[str]] = {}
    for question_id, question in questions.items():
        surface_id = question.get("surface_id")
        if surface_id not in surfaces:
            errors.append(f"question {question_id}: unknown surface_id {surface_id!r}")
            continue
        allowed = {item.get("action_id") for item in items(surfaces[surface_id], "candidate_actions", f"surface {surface_id}", errors)}
        kind = question.get("type", "choice")
        if kind not in QUESTION_TYPES:
            errors.append(f"question {question_id}: invalid type {kind!r}")
            continue
        source = question.get("criteria_source", "static")
        if source not in CRITERIA_SOURCES:
            errors.append(f"question {question_id}: invalid criteria_source {source!r}")
        executors: set[str] = set()

        def check_executor(executor: Any, label: str) -> None:
            if executor not in actions:
                errors.append(f"question {question_id}: unknown {label} {executor!r}")
            elif executor not in allowed:
                errors.append(f"question {question_id}: {label} {executor!r} is not on surface {surface_id}")
            else:
                executors.add(executor)

        if source == "dynamic":
            if kind != "choice":
                errors.append(f"question {question_id}: criteria_source dynamic requires type choice")
            check_executor(question.get("dynamic_executor_action_id"), "dynamic_executor_action_id")
        if kind == "choice":
            choice_ids: list[str] = []
            for choice in items(question, "choices", f"question {question_id}", errors):
                choice_ids.append(choice.get("id"))
                check_executor(choice.get("executor_action_id"), "executor_action_id")
            if len(set(choice_ids)) != len(choice_ids):
                errors.append(f"question {question_id}: duplicate choice id")
            if len(choice_ids) < (1 if source == "dynamic" else 2):
                errors.append(f"question {question_id}: Choice needs at least two choices")
            if question.get("abstention_choice") not in choice_ids:
                errors.append(f"question {question_id}: abstention_choice must reference a choice id")
        elif kind == "noul":
            for field in ("yes_action_id", "no_action_id"):
                check_executor(question.get(field), field)
            fallback = question.get("abstention_action_id") or question.get("no_action_id")
            if fallback not in actions:
                errors.append(f"question {question_id}: noul needs abstention_action_id or no_action_id referencing an action")
        else:
            levels = items(question, "levels", f"question {question_id}", errors)
            level_ids = [level.get("id") for level in levels]
            if len(levels) < 2:
                errors.append(f"question {question_id}: Score needs at least two levels")
            if len(set(level_ids)) != len(level_ids):
                errors.append(f"question {question_id}: duplicate level id")
            for level in levels:
                check_executor(level.get("executor_action_id"), "executor_action_id")
            if question.get("abstention_action_id") not in actions:
                errors.append(f"question {question_id}: Score needs abstention_action_id referencing an action")
        question_executors[question_id] = executors

    policy = adapter_doc.get("policy")
    if policy is None or policy == {}:
        warnings.append("jev_adapter_spec: policy is empty; every non-fallback action abstains until calibrated")
    elif not isinstance(policy, dict):
        errors.append("jev_adapter_spec: policy must be a mapping")
    else:
        def check_fraction(value: Any, owner: str) -> None:
            if value is None:
                return
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                errors.append(f"{owner}: must be a number between 0 and 1")

        check_fraction(policy.get("min_confidence"), "policy.min_confidence")
        check_fraction(policy.get("min_accuracy"), "policy.min_accuracy")
        if policy.get("default_when_uncalibrated") not in (None, "abstain", "allow"):
            errors.append("policy.default_when_uncalibrated must be abstain or allow")
        scoped = policy.get("questions") or {}
        if not isinstance(scoped, dict):
            errors.append("policy.questions must be a mapping")
        else:
            for question_id, entry in scoped.items():
                if question_id not in questions:
                    errors.append(f"policy.questions.{question_id}: unknown question")
                    continue
                if not isinstance(entry, dict):
                    errors.append(f"policy.questions.{question_id}: must be a mapping")
                    continue
                check_fraction(entry.get("min_confidence"), f"policy.questions.{question_id}.min_confidence")
                per_action = entry.get("actions") or {}
                if not isinstance(per_action, dict):
                    errors.append(f"policy.questions.{question_id}.actions must be a mapping")
                    continue
                for action_id, value in per_action.items():
                    if action_id not in question_executors.get(question_id, set()):
                        errors.append(f"policy.questions.{question_id}.actions.{action_id}: not an executor of that question")
                    check_fraction(value, f"policy.questions.{question_id}.actions.{action_id}")

    counts = {
        "actions": len(actions),
        "surface_candidates": len(candidates),
        "states": len(states),
        "transitions": len(transitions),
        "surfaces": len(surfaces),
        "questions": len(questions),
        "evidence": len(evidence),
    }
    return errors, warnings, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    errors, warnings, counts = validate(args.bundle)
    print_report(errors, warnings, counts)
    return 1 if errors else 0


def print_report(errors: list[str], warnings: list[str], counts: dict[str, int]) -> None:
    if errors:
        print(f"INVALID ({len(errors)} error(s))")
        for error in errors:
            print(f"  ERROR: {error}")
    else:
        print("VALID")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    if counts:
        print("counts: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    raise SystemExit(main())
