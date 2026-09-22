#!/usr/bin/env python3
"""Generate a Python JEV adapter from an Action Bundle.

The generated module owns the JEV boundary, answer validation, abstention,
confidence policy, precondition and state gates, the decision log, and the
handlers. Handlers are rendered from each action's evidence-graded ``binding``:
a real executor when the binding was observed running, a stub that names the
missing evidence otherwise. Nothing in the bundle is inferred at runtime.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


DEFAULT_MODEL = "jev-latest"
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTION_TYPES = {"choice", "noul", "score"}
PROVIDERS = {"vendor_neutral", "typesafe_system_one_http", "typesafe", "laya"}
CRITERIA_SOURCES = {"static", "dynamic"}
BINDING_KINDS = {"python_callable", "http", "cli", "mcp", "ui"}
# A handler is generated only when at least one binding evidence entry proves
# the invocation was seen to run. documented/verified_schema prove existence,
# not invocation, so they render a stub.
BINDING_EXECUTABLE_GRADES = {"verified_runtime", "observed_trace"}
# Every kind has a generic renderer. ui uses Playwright and mcp the MCP Python
# SDK; both are imported inside the handler and both accept a host-injected
# page / caller so an existing browser or session can be reused.
GENERATED_KINDS = {"python_callable", "http", "cli", "mcp", "ui"}


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path.name}: top-level value must be a mapping")
    return value


def evidence_grades(bundle: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    ledger = bundle / "evidence_ledger.jsonl"
    if not ledger.is_file():
        return result
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if isinstance(item, dict) and item.get("evidence_id"):
                result[item["evidence_id"]] = item.get("grade", "")
    return result


def handler_status(action: dict[str, Any], grades: dict[str, str]) -> str:
    binding = action.get("binding")
    if not isinstance(binding, dict) or not binding:
        return "stub:no_binding"
    kind = binding.get("kind")
    if kind not in GENERATED_KINDS:
        return f"stub:unsupported_kind:{kind}"
    refs = binding.get("evidence_refs") or []
    if not any(grades.get(ref) in BINDING_EXECUTABLE_GRADES for ref in refs):
        return "stub:unobserved_binding"
    return f"generated:{kind}"


def load_bundle(bundle: Path) -> dict[str, Any]:
    files = {
        "actions": "action_registry.yaml",
        "states": "state_registry.yaml",
        "surfaces": "decision_surfaces.yaml",
        "adapter": "jev_adapter_spec.yaml",
    }
    docs = {key: load_yaml(bundle / name) for key, name in files.items()}
    actions = docs["actions"].get("actions", [])
    states = docs["states"].get("states", [])
    surfaces = docs["surfaces"].get("decision_surfaces", [])
    questions = docs["adapter"].get("classifier_questions", [])
    if not isinstance(actions, list) or not isinstance(states, list) or not isinstance(surfaces, list):
        raise SystemExit("action_registry, state_registry, and decision_surfaces must contain lists")
    if not isinstance(questions, list):
        raise SystemExit("jev_adapter_spec.classifier_questions must be a list")
    action_map = {item.get("action_id"): item for item in actions if isinstance(item, dict)}
    state_map = {item.get("state_id"): item for item in states if isinstance(item, dict)}
    surface_map = {item.get("surface_id"): item for item in surfaces if isinstance(item, dict)}
    question_map = {item.get("question_id"): item for item in questions if isinstance(item, dict)}
    if not action_map or not surface_map or not question_map:
        raise SystemExit("bundle must contain actions, decision surfaces, and classifier questions")

    for question_id, question in question_map.items():
        if not question_id or not isinstance(question_id, str):
            raise SystemExit("every classifier question needs a string question_id")
        surface_id = question.get("surface_id")
        if surface_id not in surface_map:
            raise SystemExit(f"question {question_id}: unknown surface_id {surface_id!r}")
        kind = question.get("type", "choice")
        if kind not in QUESTION_TYPES:
            raise SystemExit(f"question {question_id}: unsupported type {kind!r}")
        source = question.get("criteria_source", "static")
        if source not in CRITERIA_SOURCES:
            raise SystemExit(f"question {question_id}: unsupported criteria_source {source!r}")
        if source == "dynamic":
            if kind != "choice":
                raise SystemExit(f"question {question_id}: only Choice questions can use dynamic criteria")
            if question.get("dynamic_executor_action_id") not in action_map:
                raise SystemExit(f"question {question_id}: dynamic_executor_action_id must reference an action")
        if kind == "choice":
            choices = question.get("choices", [])
            minimum = 1 if source == "dynamic" else 2
            if not isinstance(choices, list) or len(choices) < minimum:
                raise SystemExit(f"question {question_id}: Choice needs at least {minimum} static choice(s)")
            for choice in choices:
                if not isinstance(choice, dict) or not choice.get("id") or not choice.get("executor_action_id"):
                    raise SystemExit(f"question {question_id}: every Choice needs id and executor_action_id")
                if choice["executor_action_id"] not in action_map:
                    raise SystemExit(f"question {question_id}: unknown executor_action_id {choice['executor_action_id']!r}")
        elif kind == "noul":
            for field in ("yes_action_id", "no_action_id"):
                if question.get(field) not in action_map:
                    raise SystemExit(f"question {question_id}: {field} must reference an action")
        else:
            levels = question.get("levels", [])
            if not isinstance(levels, list) or len(levels) < 2:
                raise SystemExit(f"question {question_id}: Score needs at least two levels")
            for level in levels:
                if not isinstance(level, dict) or not level.get("id") or not level.get("executor_action_id"):
                    raise SystemExit(f"question {question_id}: every Score level needs id and executor_action_id")
                if level["executor_action_id"] not in action_map:
                    raise SystemExit(f"question {question_id}: unknown executor_action_id {level['executor_action_id']!r}")

    provider = docs["adapter"].get("provider", "vendor_neutral")
    if provider not in PROVIDERS:
        raise SystemExit(f"jev_adapter_spec: unsupported provider {provider!r}; use one of {sorted(PROVIDERS)}")

    grades = evidence_grades(bundle)
    for action_id, action in action_map.items():
        binding = action.get("binding")
        if binding is None:
            continue
        if not isinstance(binding, dict) or binding.get("kind") not in BINDING_KINDS:
            raise SystemExit(f"action {action_id}: binding.kind must be one of {sorted(BINDING_KINDS)}")
        if not binding.get("evidence_refs"):
            raise SystemExit(f"action {action_id}: binding without evidence_refs")

    return {
        "schema_version": docs["adapter"].get("schema_version", "1.0"),
        "system_id": docs["adapter"].get("system_id", docs["actions"].get("system_id", "generated_system")),
        "provider": docs["adapter"].get("provider", "vendor_neutral"),
        "model": docs["adapter"].get("model") or DEFAULT_MODEL,
        "endpoint": docs["adapter"].get("endpoint") or DEFAULT_ENDPOINT,
        "policy": docs["adapter"].get("policy") or {},
        "actions": action_map,
        "states": state_map,
        "surfaces": surface_map,
        "questions": question_map,
        "handler_status": {action_id: handler_status(action, grades) for action_id, action in action_map.items()},
    }


def module_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]", "_", value).strip("_").lower()
    if not value:
        raise SystemExit("module name must contain at least one alphanumeric character")
    if value[0].isdigit():
        value = f"adapter_{value}"
    return value


def _safe(action_id: str) -> str:
    return re.sub(r"\W", "_", action_id)


def render_handlers(bundle: dict[str, Any]) -> str:
    """One reviewable function per action, plus the HANDLERS table."""
    lines: list[str] = []
    for action_id, action in bundle["actions"].items():
        status = bundle["handler_status"][action_id]
        binding = action.get("binding") or {}
        locator = binding.get("locator") or binding.get("argv", [""])[0] if binding else ""
        refs = ", ".join(binding.get("evidence_refs") or []) or "none"
        name = f"_handler_{_safe(action_id)}"
        lines.append(f"def {name}(state: Mapping[str, Any]) -> Any:")
        lines.append(f'    """{status} -> {locator or "no binding"} (evidence: {refs})"""')
        if status.startswith("generated:"):
            kind = status.split(":", 1)[1]
            lines.append(f"    return _call_{kind}({action_id!r}, state)")
        else:
            if status == "stub:no_binding":
                hint = f"add a binding with verified_runtime or observed_trace evidence to action {action_id} and regenerate"
            elif status == "stub:unobserved_binding":
                hint = (
                    f"binding {locator!r} has no verified_runtime or observed_trace evidence "
                    f"(current refs: {refs}); add one to the ledger and regenerate"
                )
            else:
                hint = f"binding kind is not supported by this generator; set HANDLERS[{action_id!r}] = your_callable"
            lines.append(f"    raise HandlerUnavailable({action_id!r}, {status!r}, {hint!r})")
        lines.append("")
        lines.append("")
    table = ", ".join(f"{action_id!r}: _handler_{_safe(action_id)}" for action_id in bundle["actions"])
    lines.append(f"HANDLERS: dict[str, Callable[[Mapping[str, Any]], Any]] = {{{table}}}")
    return "\n".join(lines)


TEMPLATE = '''"""Generated JEV adapter for @@SYSTEM_ID@@.

Generated by action-vocabulary-forge. Keep this file reviewable. HANDLERS is
rendered from the bundle bindings; override an entry to replace a stub. Do not
put credentials in this file: secrets are read from the environment at call time.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping


DEFAULT_ENDPOINT = @@ENDPOINT@@
MODEL = @@MODEL@@
PROVIDER = @@PROVIDER@@
# Providers with a small context window read the compact wording when a question carries one.
# FORGE_COMPACT_CRITERIA=1|0 forces it on or off.
COMPACT_PROVIDERS = {"laya"}
UNCALIBRATED = float("inf")
BUNDLE = @@BUNDLE@@
ACTIONS = BUNDLE["actions"]
STATES = BUNDLE["states"]
SURFACES = BUNDLE["surfaces"]
QUESTIONS = BUNDLE["questions"]
POLICY = BUNDLE.get("policy") or {}
HANDLER_STATUS: dict[str, str] = BUNDLE["handler_status"]

DynamicChoices = Mapping[str, Mapping[str, str]]


class AdapterError(RuntimeError):
    """Base class for a rejected or malformed decision."""


class IllegalChoice(AdapterError):
    pass


class ExecutionBlocked(AdapterError):
    pass


class HandlerError(AdapterError):
    """A generated handler attempted the side effect and it failed."""


class HandlerUnavailable(ExecutionBlocked):
    """A stub handler was called."""

    def __init__(self, action_id: str, status: str, hint: str) -> None:
        super().__init__(f"action {action_id}: {status}; {hint}")
        self.action_id = action_id
        self.status = status


# --- predicates (embedded from scripts/predicates.py) ---------------------
@@PREDICATES@@
# --- end predicates ---------------------------------------------------------


# --- transports (embedded from scripts/transports.py) ----------------------
@@TRANSPORTS@@
# --- end transports ---------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    question_id: str
    action_id: str
    confidence: float | None
    probabilities: Mapping[str, float]
    abstained: bool = False
    reason: str | None = None
    raw_answer: Mapping[str, Any] | None = None
    selected_choice: str | None = None
    proposed_action_id: str | None = None
    threshold: float | None = None
    model: str | None = None
    latency_ms: float | None = None
    usage: Mapping[str, Any] | None = None
    case_id: str | None = None


# --- state and preconditions -------------------------------------------------

def check_preconditions(action_id: str, state: Mapping[str, Any]) -> list[str]:
    """Return the action preconditions that do not hold for ``state``."""
    action = ACTIONS.get(action_id)
    if not action:
        raise ExecutionBlocked(f"unknown action: {action_id}")
    return evaluate_all([p for p in action.get("preconditions", []) if isinstance(p, str)], state)


def infer_state(state: Mapping[str, Any]) -> str | None:
    """The unique registered state whose observable_predicate holds, else None."""
    matches = []
    for state_id, item in STATES.items():
        predicate = item.get("observable_predicate")
        if isinstance(predicate, str):
            try:
                if evaluate_predicate(predicate, state):
                    matches.append(state_id)
            except PredicateError:
                continue
    return matches[0] if len(matches) == 1 else None


def current_state_id(state: Mapping[str, Any]) -> str | None:
    value = state.get("state_id")
    return value if value is not None else infer_state(state)


def legal_actions(state: Mapping[str, Any]) -> list[str]:
    """Actions allowed from the current state whose preconditions hold."""
    state_id = current_state_id(state)
    result = []
    for action_id, action in ACTIONS.items():
        if state_id is not None and state_id not in action.get("allowed_from_states", []):
            continue
        if check_preconditions(action_id, state):
            continue
        result.append(action_id)
    return result


# --- questions ------------------------------------------------------------------

def use_compact() -> bool:
    """True when the active provider should get the compact wording of a question."""
    forced = os.environ.get("FORGE_COMPACT_CRITERIA")
    if forced is not None:
        return forced not in ("", "0", "false", "False")
    return (os.environ.get("FORGE_PROVIDER") or PROVIDER) in COMPACT_PROVIDERS


def _text(item: Mapping[str, Any], field: str, default: Any = None) -> Any:
    """The compact variant of a field when one exists and the provider wants it."""
    if use_compact():
        compact = item.get(field + "_compact")
        if compact:
            return compact
    return item.get(field, default)


def _criteria(question: Mapping[str, Any], dynamic: Mapping[str, str] | None = None) -> Any:
    kind = question.get("type", "choice")
    if kind == "choice":
        criteria = {item["id"]: _text(item, "criterion", item["id"]) for item in question["choices"]}
        if dynamic:
            if question.get("criteria_source", "static") != "dynamic":
                raise IllegalChoice(f"question {question['question_id']} is static and does not accept dynamic choices")
            collision = set(criteria) & set(dynamic)
            if collision:
                raise AdapterError(f"dynamic choice ids collide with static ids: {sorted(collision)}")
            criteria.update({str(k): str(v) for k, v in dynamic.items()})
        if question.get("criteria_source", "static") == "dynamic" and len(criteria) < 2:
            raise AdapterError(f"question {question['question_id']}: dynamic Choice needs at least two criteria in total")
        return criteria
    if kind == "score":
        return [_text(item, "criterion", item["id"]) for item in question["levels"]]
    return None


def build_payload(
    context: Any,
    *,
    question_id: str | None = None,
    dynamic_choices: DynamicChoices | None = None,
    model: str = MODEL,
) -> dict[str, Any]:
    """Build a provider request; context never widens the legal choice set."""
    selected = {question_id: QUESTIONS[question_id]} if question_id else QUESTIONS
    if question_id and question_id not in QUESTIONS:
        raise IllegalChoice(f"unknown question: {question_id}")
    questions = {}
    for qid, question in selected.items():
        item = {
            "type": question.get("type", "choice"),
            "instructions": _text(question, "instruction") or _text(question, "instructions") or "Choose the best supported result.",
        }
        criteria = _criteria(question, (dynamic_choices or {}).get(qid))
        if criteria is not None:
            item["criteria"] = criteria
        questions[qid] = item
    return {
        "model": model,
        "state": {"context": context, "system_id": BUNDLE["system_id"]},
        "questions": questions,
    }


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def parse_response(
    response: Mapping[str, Any],
    question_id: str,
    *,
    dynamic_choices: DynamicChoices | None = None,
) -> Decision:
    """Convert one raw JEV answer into a validated action ID."""
    question = QUESTIONS.get(question_id)
    if not question:
        raise IllegalChoice(f"unknown question: {question_id}")
    answers = response.get("answers")
    if not isinstance(answers, Mapping):
        raise IllegalChoice("JEV response omitted answers")
    answer = answers.get(question_id)
    if not isinstance(answer, Mapping):
        raise IllegalChoice(f"JEV response omitted answer {question_id!r}")
    kind = question.get("type", "choice")
    probabilities = answer.get("probabilities") or {}
    if not isinstance(probabilities, Mapping):
        probabilities = {}
    probabilities = {str(k): float(v) for k, v in probabilities.items() if _number(v) is not None}
    confidence = _number(answer.get("confidence"))
    selected_choice: str | None = None
    if kind == "choice":
        raw_choice = answer.get("choice")
        choices = {item["id"]: item["executor_action_id"] for item in question["choices"]}
        dynamic = (dynamic_choices or {}).get(question_id) or {}
        if dynamic and question.get("criteria_source", "static") != "dynamic":
            raise IllegalChoice(f"question {question_id} is static and does not accept dynamic choices")
        if raw_choice in choices:
            selected = choices[raw_choice]
        elif raw_choice in dynamic:
            selected = question["dynamic_executor_action_id"]
        else:
            raise IllegalChoice(f"JEV returned illegal choice {raw_choice!r} for {question_id}")
        selected_choice = str(raw_choice)
        abstention = raw_choice == question.get("abstention_choice")
    elif kind == "noul":
        value = _number(answer.get("noul"))
        if value is None:
            raise IllegalChoice(f"JEV returned no Noul value for {question_id}")
        selected = question["yes_action_id"] if value >= float(question.get("yes_threshold", 0.5)) else question["no_action_id"]
        abstention = selected == question.get("abstention_action_id")
        confidence = value if value >= 0.5 else 1.0 - value
    else:
        score = _number(answer.get("score"))
        levels = question["levels"]
        if score is None:
            raise IllegalChoice(f"JEV returned no Score value for {question_id}")
        index = max(0, min(len(levels) - 1, round(score)))
        selected = levels[index]["executor_action_id"]
        selected_choice = levels[index]["id"]
        abstention = selected == question.get("abstention_action_id")
    return Decision(
        question_id,
        selected,
        confidence,
        probabilities,
        abstention,
        raw_answer=answer,
        selected_choice=selected_choice,
        proposed_action_id=selected,
        model=response.get("model"),
        usage=response.get("usage") if isinstance(response.get("usage"), Mapping) else None,
    )


# --- policy -----------------------------------------------------------------------

def question_fallbacks(question_id: str) -> set[str]:
    """The actions this question may fall back to, and which are therefore never gated.

    A Noul's ``no_action_id`` counts only when the question declares no
    ``abstention_action_id``. Otherwise "no" is a conclusion like any other and must clear its
    own threshold: a control test that can record an exception ungated is not a control test.
    """
    question = QUESTIONS[question_id]
    values = {question.get("abstention_action_id")}
    if not question.get("abstention_action_id"):
        values.add(question.get("no_action_id"))
    values.add(question.get("abstention_choice"))
    if question.get("type", "choice") == "choice":
        for item in question.get("choices", []):
            if item.get("id") == question.get("abstention_choice"):
                values.add(item.get("executor_action_id"))
    return {value for value in values if value}


def _fallback_action(question: Mapping[str, Any]) -> str | None:
    fallback = question.get("abstention_choice") or question.get("abstention_action_id") or question.get("no_action_id")
    # (abstention_choice is a Choice id; the next line resolves it to its executor action)
    if fallback and question.get("type", "choice") == "choice":
        fallback = next((item["executor_action_id"] for item in question["choices"] if item["id"] == fallback), fallback)
    return fallback


def threshold_for(question_id: str, action_id: str) -> float | None:
    """Resolve policy.questions[q].actions[a] -> questions[q] -> global -> uncalibrated default."""
    if action_id in question_fallbacks(question_id):
        return 0.0
    scoped = (POLICY.get("questions") or {}).get(question_id) or {}
    for value in ((scoped.get("actions") or {}).get(action_id), scoped.get("min_confidence"), POLICY.get("min_confidence")):
        if value is not None:
            return float(value)
    return None if POLICY.get("default_when_uncalibrated") == "allow" else UNCALIBRATED


def apply_policy(decision: Decision, *, min_confidence: float | None = None) -> Decision:
    """Route low-confidence or uncalibrated results to the declared abstention action."""
    proposed = decision.proposed_action_id or decision.action_id
    threshold = float(min_confidence) if min_confidence is not None else threshold_for(decision.question_id, decision.action_id)
    if threshold is None:
        return replace(decision, proposed_action_id=proposed, threshold=None)
    confidence = decision.confidence if decision.confidence is not None else 0.0
    if threshold != UNCALIBRATED and confidence >= threshold:
        return replace(decision, proposed_action_id=proposed, threshold=threshold)
    question = QUESTIONS[decision.question_id]
    fallback = _fallback_action(question)
    if not fallback:
        raise ExecutionBlocked(f"question {decision.question_id} has no abstention action")
    reason = "uncalibrated:abstain" if threshold == UNCALIBRATED else f"confidence_below_threshold:{threshold}"
    return replace(
        decision,
        action_id=fallback,
        abstained=True,
        reason=reason,
        proposed_action_id=proposed,
        threshold=None if threshold == UNCALIBRATED else threshold,
    )


# --- decision log ------------------------------------------------------------------

class DecisionLog:
    """Append-only JSONL log; one record per decision or execution attempt."""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = str(path or os.environ.get("FORGE_DECISION_LOG") or "decision_log.jsonl")

    def append(self, record: Mapping[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record), ensure_ascii=False, default=str) + "\\n")
            handle.flush()


def default_log() -> DecisionLog | None:
    """A log only when FORGE_DECISION_LOG is set; otherwise callers pass log= explicitly."""
    path = os.environ.get("FORGE_DECISION_LOG")
    return DecisionLog(path) if path else None


def _case_id(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def decision_record(
    decision: Decision,
    state: Mapping[str, Any],
    *,
    case_id: str | None = None,
    executed: bool = False,
    outcome: str | None = None,
    blocked_reason: str | None = None,
    result: Any = None,
) -> dict[str, Any]:
    question = QUESTIONS.get(decision.question_id) or {}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system_id": BUNDLE["system_id"],
        "question_id": decision.question_id,
        "surface_id": question.get("surface_id"),
        "case_id": case_id or decision.case_id or _case_id(state),
        "state_id": current_state_id(state),
        "selected_choice": decision.selected_choice,
        "proposed_action_id": decision.proposed_action_id or decision.action_id,
        "action_id": decision.action_id,
        "confidence": decision.confidence,
        "probabilities": dict(decision.probabilities),
        "threshold": decision.threshold,
        "abstained": decision.abstained,
        "reason": decision.reason,
        "legal_actions": legal_actions(state),
        "executed": executed,
        "outcome": outcome,
        "blocked_reason": blocked_reason,
        "result_summary": None if result is None else repr(result)[:200],
        "handler_status": HANDLER_STATUS.get(decision.action_id),
        "model": decision.model,
        "latency_ms": decision.latency_ms,
        "usage": dict(decision.usage) if decision.usage else None,
        "ground_truth_action_id": None,
        "label_source": None,
    }


# --- handlers ----------------------------------------------------------------------

def _resolve(path: str, call_state: Mapping[str, Any]) -> Any:
    value: Any = call_state
    for key in str(path).split("."):
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        else:
            raise ExecutionBlocked(f"missing argument source {path!r} in call state")
    return value


def _arguments(action_id: str, call_state: Mapping[str, Any]) -> dict[str, Any]:
    """Arguments from binding.arg_mapping, else parameters[].name looked up in the call state."""
    action = ACTIONS[action_id]
    binding = action.get("binding") or {}
    mapping = binding.get("arg_mapping")
    if isinstance(mapping, Mapping) and mapping:
        return {name: _resolve(path, call_state) for name, path in mapping.items()}
    result: dict[str, Any] = {}
    for parameter in action.get("parameters", []):
        name = parameter.get("name") if isinstance(parameter, Mapping) else None
        if not name:
            continue
        if name in call_state:
            result[name] = call_state[name]
        elif parameter.get("required", False):
            raise ExecutionBlocked(f"action {action_id}: required parameter {name!r} missing from call state")
    return result


def _flat(arguments: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(value) for key, value in arguments.items()}


def _call_python_callable(action_id: str, call_state: Mapping[str, Any]) -> Any:
    binding = ACTIONS[action_id]["binding"]
    module_path, _, attribute = str(binding["locator"]).partition(":")
    if not module_path or not attribute:
        raise ExecutionBlocked(f"action {action_id}: python_callable locator must be 'module:callable'")
    target: Any = importlib.import_module(module_path)
    for part in attribute.split("."):
        target = getattr(target, part)
    try:
        return target(**_arguments(action_id, call_state))
    except ExecutionBlocked:
        raise
    except Exception as exc:  # the side effect was attempted
        raise HandlerError(f"action {action_id}: {binding['locator']} raised {exc!r}") from exc


def _call_http(action_id: str, call_state: Mapping[str, Any]) -> Any:
    binding = ACTIONS[action_id]["binding"]
    arguments = _arguments(action_id, call_state)
    headers = {"Content-Type": "application/json", **{str(k): str(v) for k, v in (binding.get("headers") or {}).items()}}
    auth_env = binding.get("auth_env")
    if auth_env:
        token = os.environ.get(str(auth_env))
        if not token:
            raise ExecutionBlocked(f"action {action_id}: environment variable {auth_env} is not set")
        headers["Authorization"] = f"Bearer {token}"
    try:
        url = str(binding["locator"]).format(**_flat(arguments))
    except KeyError as exc:
        raise ExecutionBlocked(f"action {action_id}: URL template needs argument {exc}") from exc
    body_spec = binding.get("body")
    data = None
    if isinstance(body_spec, Mapping) and body_spec:
        data = json.dumps({key: _resolve(path, {**call_state, **arguments}) for key, path in body_spec.items()}, default=str).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=str(binding.get("method", "POST")).upper())
    try:
        with urllib.request.urlopen(request, timeout=float(binding.get("timeout_seconds", 30))) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise HandlerError(f"action {action_id}: HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise HandlerError(f"action {action_id}: request to {url} failed: {exc.reason}") from exc
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return text


def _call_cli(action_id: str, call_state: Mapping[str, Any]) -> Any:
    binding = ACTIONS[action_id]["binding"]
    arguments = _flat(_arguments(action_id, call_state))
    try:
        argv = [str(part).format(**arguments) for part in binding["argv"]]
    except KeyError as exc:
        raise ExecutionBlocked(f"action {action_id}: argv template needs argument {exc}") from exc
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    for key in binding.get("env_allowlist") or []:
        if key in os.environ:
            env[str(key)] = os.environ[key]
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            cwd=binding.get("cwd") or None,
            env=env,
            timeout=float(binding.get("timeout_seconds", 30)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HandlerError(f"action {action_id}: {argv[0]} failed to run: {exc}") from exc
    if completed.returncode != 0:
        raise HandlerError(f"action {action_id}: {argv[0]} exited {completed.returncode}: {completed.stderr.strip()[:500]}")
    return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


# --- ui (Playwright) and mcp (MCP SDK) ----------------------------------------------------
# Hosts may inject what they already have; the SDKs are only imported when nothing was injected.
_UI_PAGE: Any = None
_MCP_CALLER: Callable[[str, str, Mapping[str, Any], Mapping[str, Any]], Any] | None = None
UI_OPERATIONS = {"goto", "click", "fill", "select", "press", "check", "uncheck", "read"}


def set_ui_page(page: Any) -> None:
    """Reuse a Playwright Page (sync API) the host already owns; None resets."""
    global _UI_PAGE
    _UI_PAGE = page


def set_mcp_caller(caller: Callable[[str, str, Mapping[str, Any], Mapping[str, Any]], Any] | None) -> None:
    """Route MCP calls through the host: caller(server, tool, arguments, binding) -> result."""
    global _MCP_CALLER
    _MCP_CALLER = caller


def _ui_operate(page: Any, action_id: str, binding: Mapping[str, Any], arguments: Mapping[str, Any]) -> Any:
    operation = str(binding.get("operation", "click"))
    if operation not in UI_OPERATIONS:
        raise ExecutionBlocked(f"action {action_id}: unsupported ui operation {operation!r}")
    flat = _flat(arguments)
    timeout_ms = float(binding.get("timeout_seconds", 30)) * 1000.0
    url = binding.get("url")
    if url:
        try:
            page.goto(str(url).format(**flat), timeout=timeout_ms)
        except KeyError as exc:
            raise ExecutionBlocked(f"action {action_id}: url template needs argument {exc}") from exc
    if operation == "goto":
        return {"url": page.url if hasattr(page, "url") else url}
    try:
        selector = str(binding["locator"]).format(**flat)
    except KeyError as exc:
        raise ExecutionBlocked(f"action {action_id}: locator template needs argument {exc}") from exc
    target = page.locator(selector)
    value = arguments.get("value")
    if operation in {"fill", "select", "press"} and value is None:
        raise ExecutionBlocked(f"action {action_id}: ui operation {operation} needs a 'value' argument (use arg_mapping)")
    if operation == "click":
        target.click(timeout=timeout_ms)
    elif operation == "fill":
        target.fill(str(value), timeout=timeout_ms)
    elif operation == "select":
        target.select_option(str(value), timeout=timeout_ms)
    elif operation == "press":
        target.press(str(value), timeout=timeout_ms)
    elif operation == "check":
        target.check(timeout=timeout_ms)
    elif operation == "uncheck":
        target.uncheck(timeout=timeout_ms)
    else:
        return {"selector": selector, "text": target.inner_text(timeout=timeout_ms)}
    return {"selector": selector, "operation": operation, "url": getattr(page, "url", None)}


def _call_ui(action_id: str, call_state: Mapping[str, Any]) -> Any:
    binding = ACTIONS[action_id]["binding"]
    arguments = _arguments(action_id, call_state)
    if _UI_PAGE is not None:
        try:
            return _ui_operate(_UI_PAGE, action_id, binding, arguments)
        except ExecutionBlocked:
            raise
        except Exception as exc:
            raise HandlerError(f"action {action_id}: ui operation failed: {exc!r}") from exc
    if not binding.get("url"):
        raise ExecutionBlocked(f"action {action_id}: no Playwright page injected (set_ui_page) and binding has no url to open")
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise ExecutionBlocked(f"action {action_id}: playwright is not installed (pip install playwright && playwright install chromium) or inject a page with set_ui_page") from exc
    try:
        with sync_playwright() as pw:
            browser = getattr(pw, str(binding.get("browser", "chromium"))).launch(headless=bool(binding.get("headless", True)))
            try:
                return _ui_operate(browser.new_page(), action_id, binding, arguments)
            finally:
                browser.close()
    except ExecutionBlocked:
        raise
    except Exception as exc:
        raise HandlerError(f"action {action_id}: ui operation failed: {exc!r}") from exc


def _mcp_result(result: Any) -> Any:
    """Flatten an MCP CallToolResult into plain data."""
    content = getattr(result, "content", result)
    if isinstance(content, list):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            parts.append(text if text is not None else (item if isinstance(item, (str, dict)) else repr(item)))
        return parts[0] if len(parts) == 1 else parts
    return content


def _call_mcp(action_id: str, call_state: Mapping[str, Any]) -> Any:
    binding = ACTIONS[action_id]["binding"]
    arguments = _arguments(action_id, call_state)
    server, tool = str(binding.get("server")), str(binding.get("tool"))
    if _MCP_CALLER is not None:
        try:
            return _MCP_CALLER(server, tool, arguments, binding)
        except ExecutionBlocked:
            raise
        except Exception as exc:
            raise HandlerError(f"action {action_id}: mcp tool {server}/{tool} failed: {exc!r}") from exc
    transport = str(binding.get("transport", "stdio"))
    headers: dict[str, str] = {str(k): str(v) for k, v in (binding.get("headers") or {}).items()}
    auth_env = binding.get("auth_env")
    if auth_env:
        token = os.environ.get(str(auth_env))
        if not token:
            raise ExecutionBlocked(f"action {action_id}: environment variable {auth_env} is not set")
        headers["Authorization"] = f"Bearer {token}"
    try:
        import asyncio  # noqa: PLC0415
        from mcp import ClientSession  # type: ignore
        if transport == "stdio":
            from mcp import StdioServerParameters  # type: ignore
            from mcp.client.stdio import stdio_client  # type: ignore
        else:
            from mcp.client.streamable_http import streamablehttp_client  # type: ignore
    except ImportError as exc:
        raise ExecutionBlocked(f"action {action_id}: the mcp package is not installed (pip install mcp) or inject a caller with set_mcp_caller") from exc

    async def call() -> Any:
        if transport == "stdio":
            command = binding.get("command")
            if not command:
                raise ExecutionBlocked(f"action {action_id}: stdio mcp binding needs command")
            env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
            for key in binding.get("env_allowlist") or []:
                if key in os.environ:
                    env[str(key)] = os.environ[key]
            params = StdioServerParameters(command=str(command), args=[str(a) for a in binding.get("args") or []], env=env, cwd=binding.get("cwd"))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(tool, dict(arguments))
        url = binding.get("url")
        if not url:
            raise ExecutionBlocked(f"action {action_id}: http mcp binding needs url")
        async with streamablehttp_client(str(url), headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, dict(arguments))

    try:
        result = asyncio.run(asyncio.wait_for(call(), timeout=float(binding.get("timeout_seconds", 30))))
    except ExecutionBlocked:
        raise
    except Exception as exc:
        raise HandlerError(f"action {action_id}: mcp tool {server}/{tool} failed: {exc!r}") from exc
    if getattr(result, "isError", False):
        raise HandlerError(f"action {action_id}: mcp tool {server}/{tool} returned an error: {_mcp_result(result)!r}")
    return _mcp_result(result)


@@HANDLERS@@


# --- execution ------------------------------------------------------------------------

def execute(
    decision: Decision,
    state: Mapping[str, Any],
    *,
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
    guard: Callable[[str, Mapping[str, Any]], bool] | None = None,
    confirmed: bool = False,
    check_preconditions: bool = True,
    log: DecisionLog | None = None,
    case_id: str | None = None,
) -> Any:
    """Execute only a validated action, through the generated or host-supplied handler."""
    table = HANDLERS if handlers is None else handlers
    record = decision_record(decision, state, case_id=case_id)
    executed = False
    outcome: str | None = None
    blocked: str | None = None
    result: Any = None
    try:
        action = ACTIONS.get(decision.action_id)
        if not action:
            raise ExecutionBlocked(f"unknown action: {decision.action_id}")
        current_state = record["state_id"]
        allowed_states = action.get("allowed_from_states", [])
        if current_state is not None and current_state not in allowed_states:
            raise ExecutionBlocked(f"action {decision.action_id} is illegal from state {current_state}")
        if action.get("requires_confirmation") and not confirmed:
            raise ExecutionBlocked(f"action {decision.action_id} requires confirmation")
        if check_preconditions:
            failed = globals()["check_preconditions"](decision.action_id, state)
            if failed:
                raise ExecutionBlocked(f"preconditions failed for {decision.action_id}: {failed}")
        else:
            record["reason"] = (record["reason"] or "") + "|preconditions_skipped"
        if guard is not None and not guard(decision.action_id, state):
            raise ExecutionBlocked(f"deterministic guard rejected {decision.action_id}")
        if decision.abstained and decision.action_id not in question_fallbacks(decision.question_id):
            raise ExecutionBlocked("abstained decision is not a declared fallback")
        handler = table.get(decision.action_id)
        if handler is None:
            raise ExecutionBlocked(f"no handler registered for {decision.action_id}")
        result = handler({**state, "decision": record})
        executed = True
        outcome = "ok"
        return result
    except ExecutionBlocked as exc:
        outcome = "blocked"
        blocked = str(exc)
        raise
    except Exception as exc:
        outcome = "error"
        blocked = str(exc)
        raise
    finally:
        if log is not None:
            record.update(executed=executed, outcome=outcome, blocked_reason=blocked)
            record["result_summary"] = None if result is None else repr(result)[:200]
            log.append(record)


def _http_transport(endpoint: str | None, api_key: str | None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    token = api_key or os.environ.get("TYPESAFE_API_KEY")
    if not token:
        raise AdapterError("TYPESAFE_API_KEY is not set")
    url = endpoint or os.environ.get("TYPESAFE_API_URL", DEFAULT_ENDPOINT)

    def send(payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise AdapterError(f"JEV request failed: {exc}") from exc

    return send


def default_transport(*, endpoint: str | None = None, api_key: str | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The transport named by FORGE_PROVIDER (env) or the bundle's provider; TypeSafe HTTP otherwise."""
    provider = os.environ.get("FORGE_PROVIDER") or PROVIDER
    if provider == "laya":
        model = MODEL if MODEL.startswith("laya:") else os.environ.get("FORGE_LAYA_MODEL", "laya:typed-decisions")
        send = laya_transport(model)

        def guarded(payload: dict[str, Any]) -> dict[str, Any]:
            try:
                return send(payload)
            except RuntimeError as exc:  # missing SDK or a malformed local answer
                raise AdapterError(str(exc)) from exc

        return guarded
    return _http_transport(endpoint, api_key)


def classify(
    context: Any,
    *,
    question_id: str | None = None,
    dynamic_choices: DynamicChoices | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Decision:
    """Call JEV and return one policy-applied decision; require one question when unspecified."""
    if question_id is None:
        if len(QUESTIONS) != 1:
            raise AdapterError("question_id is required when the bundle has multiple classifier questions")
        question_id = next(iter(QUESTIONS))
    payload = build_payload(context, question_id=question_id, dynamic_choices=dynamic_choices)
    send = transport or default_transport(endpoint=endpoint, api_key=api_key)
    started = time.perf_counter()
    body = send(payload)
    latency_ms = (time.perf_counter() - started) * 1000.0
    decision = parse_response(body, question_id, dynamic_choices=dynamic_choices)
    decision = replace(decision, latency_ms=latency_ms, case_id=_case_id(context))
    return apply_policy(decision)


def decide(
    context: Any,
    state: Mapping[str, Any],
    *,
    question_id: str | None = None,
    dynamic_choices: DynamicChoices | None = None,
    case_id: str | None = None,
    log: DecisionLog | None = None,
    transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
) -> Decision:
    """classify + policy; logs the decision without executing it."""
    decision = classify(context, question_id=question_id, dynamic_choices=dynamic_choices, endpoint=endpoint, api_key=api_key, transport=transport)
    if case_id:
        decision = replace(decision, case_id=case_id)
    if log is not None:
        log.append(decision_record(decision, state, case_id=case_id))
    return decision


def run(
    context: Any,
    state: Mapping[str, Any],
    *,
    question_id: str | None = None,
    dynamic_choices: DynamicChoices | None = None,
    case_id: str | None = None,
    log: DecisionLog | None = None,
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
    guard: Callable[[str, Mapping[str, Any]], bool] | None = None,
    confirmed: bool = False,
    transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
) -> tuple[Decision, Any]:
    """decide + execute with exactly one log record."""
    decision = decide(context, state, question_id=question_id, dynamic_choices=dynamic_choices, case_id=case_id, log=None, transport=transport, endpoint=endpoint, api_key=api_key)
    result = execute(decision, state, handlers=handlers, guard=guard, confirmed=confirmed, log=log, case_id=case_id)
    return decision, result


__all__ = [
    "ACTIONS", "BUNDLE", "Decision", "DecisionLog", "HANDLERS", "HANDLER_STATUS", "QUESTIONS", "STATES", "SURFACES",
    "AdapterError", "IllegalChoice", "ExecutionBlocked", "HandlerError", "HandlerUnavailable", "PredicateError",
    "apply_policy", "build_payload", "check_preconditions", "classify", "decide", "decision_record", "default_log",
    "execute", "infer_state", "legal_actions", "parse_response", "question_fallbacks", "run", "threshold_for",
    "set_ui_page", "set_mcp_caller", "default_transport", "laya_transport", "PROVIDER", "use_compact",
]
'''


def render(bundle: dict[str, Any], module: str) -> str:
    predicates = (Path(__file__).with_name("predicates.py")).read_text(encoding="utf-8")
    # Drop the module docstring and imports already present in the template.
    body = predicates.split('"""', 2)[2]
    body = "\n".join(
        line for line in body.splitlines()
        if not line.startswith(("from __future__", "import re", "from typing"))
    ).strip("\n")
    body = "import re\n\n" + body
    transports = (Path(__file__).with_name("transports.py")).read_text(encoding="utf-8")
    tbody = transports.split('"""', 2)[2]
    tbody = "\n".join(
        line for line in tbody.splitlines()
        if not line.startswith(("from __future__", "import json", "import threading", "from typing"))
    ).strip("\n")
    tbody = "import threading\n\n" + tbody
    return (
        TEMPLATE.replace("@@SYSTEM_ID@@", str(bundle["system_id"]))
        .replace("@@ENDPOINT@@", repr(bundle["endpoint"]))
        .replace("@@MODEL@@", repr(bundle["model"]))
        .replace("@@PROVIDER@@", repr(bundle["provider"]))
        .replace("@@TRANSPORTS@@", tbody)
        .replace("@@BUNDLE@@", repr(bundle))
        .replace("@@PREDICATES@@", body)
        .replace("@@HANDLERS@@", render_handlers(bundle))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="generated Python module path")
    parser.add_argument("--module-name", default=None, help="module label used in the generated header")
    args = parser.parse_args()
    bundle = load_bundle(args.bundle)
    label = module_name(args.module_name or args.output.stem)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(bundle, label), encoding="utf-8")
    generated = sum(1 for status in bundle["handler_status"].values() if status.startswith("generated:"))
    print(
        f"generated Python adapter {args.output} for {bundle['system_id']} "
        f"({len(bundle['questions'])} question(s), {len(bundle['actions'])} action(s), "
        f"{generated} generated handler(s), {len(bundle['actions']) - generated} stub(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
