#!/usr/bin/env python3
"""Compile and run source-grounded judgments before a legal JEV action choice.

The semantic stage never authorizes an action. Runtime state and Action Bundle
preconditions determine legality; JEV receives only the remaining Choice options.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicates import evaluate_all, evaluate_predicate  # noqa: E402
import validate_semantic_bundle  # noqa: E402


Transport = Callable[[dict[str, Any]], dict[str, Any]]
RELEASED_JUDGMENT_MATURITIES = {"reviewed", "calibrated"}


class SemanticRuntimeError(RuntimeError):
    """The semantic bundle or runtime state cannot produce a safe decision plan."""


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SemanticRuntimeError(f"{path.name}: top-level value must be a mapping")
    return value


def _by_id(values: Any, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        return {}
    return {item[field]: item for item in values if isinstance(item, dict) and isinstance(item.get(field), str)}


def _infer_state(state_doc: dict[str, Any], state: Mapping[str, Any]) -> str | None:
    explicit = state.get("state_id")
    if isinstance(explicit, str):
        return explicit
    matches = []
    for item in state_doc.get("states", []):
        if not isinstance(item, dict):
            continue
        predicate = item.get("observable_predicate")
        if isinstance(predicate, str) and evaluate_predicate(predicate, state):
            matches.append(item.get("state_id"))
    return matches[0] if len(matches) == 1 else None


def _active_judgments(
    registry: dict[str, Any], surface: dict[str, Any], state: Mapping[str, Any]
) -> list[dict[str, Any]]:
    judgments = _by_id(registry.get("judgments"), "judgment_id")
    result = []
    for judgment_id in surface.get("supporting_judgments", []):
        item = judgments.get(judgment_id)
        if not item or item.get("maturity") not in RELEASED_JUDGMENT_MATURITIES:
            continue
        if evaluate_all(item.get("activate_when", []), state):
            continue
        result.append(item)
    return result


def _question(item: Mapping[str, Any]) -> dict[str, Any]:
    question = {"type": item["type"], "instructions": item["instructions"]}
    criteria = item.get("criteria")
    if criteria is not None:
        question["criteria"] = criteria
    return question


def _legal_actions(
    actions: dict[str, dict[str, Any]], surface: Mapping[str, Any], state_id: str, state: Mapping[str, Any]
) -> set[str]:
    offered = {
        item.get("action_id")
        for item in surface.get("candidate_actions", [])
        if isinstance(item, dict)
    }
    legal = set()
    for action_id in offered:
        action = actions.get(action_id)
        if not action or state_id not in action.get("allowed_from_states", []):
            continue
        if evaluate_all(action.get("preconditions", []), state):
            continue
        legal.add(action_id)
    return legal


def compile_plan(
    bundle: str | Path,
    context: Any,
    state: Mapping[str, Any],
    *,
    surface_id: str | None = None,
) -> dict[str, Any]:
    """Return a two-stage JEV plan containing only reviewed judgments and legal actions."""
    if not isinstance(state, Mapping):
        raise SemanticRuntimeError("runtime state must be a mapping")
    bundle = Path(bundle)
    errors, _, _ = validate_semantic_bundle.validate(bundle)
    if errors:
        raise SemanticRuntimeError("invalid semantic bundle: " + "; ".join(errors))

    registry = load_yaml(bundle / "judgment_registry.yaml")
    actions_doc = load_yaml(bundle / "action_registry.yaml")
    state_doc = load_yaml(bundle / "state_registry.yaml")
    surfaces_doc = load_yaml(bundle / "decision_surfaces.yaml")
    adapter_doc = load_yaml(bundle / "jev_adapter_spec.yaml")
    system_id = adapter_doc.get("system_id")
    model = adapter_doc.get("model", "jev-latest")

    current_state = _infer_state(state_doc, state)
    if current_state is None:
        raise SemanticRuntimeError("runtime state does not identify exactly one registered state")
    surfaces = _by_id(surfaces_doc.get("decision_surfaces"), "surface_id")
    if surface_id is None:
        matches = [
            key
            for key, item in surfaces.items()
            if item.get("production") and (item.get("activation") or {}).get("state_id") == current_state
        ]
        if len(matches) != 1:
            raise SemanticRuntimeError(
                f"state {current_state!r} activates {len(matches)} production surfaces; pass surface_id"
            )
        surface_id = matches[0]
    surface = surfaces.get(surface_id)
    if not surface:
        raise SemanticRuntimeError(f"unknown surface: {surface_id}")
    if not surface.get("production"):
        raise SemanticRuntimeError(f"surface {surface_id} is not production-reviewed")
    if (surface.get("activation") or {}).get("state_id") != current_state:
        raise SemanticRuntimeError(f"surface {surface_id} is not active in state {current_state}")

    active = _active_judgments(registry, surface, state)
    judgment_questions = {item["judgment_id"]: _question(item) for item in active}
    judgment_request = {
        "model": model,
        "state": {"context": context, "runtime_state": dict(state), "system_id": system_id},
        "questions": judgment_questions,
    }

    questions = [
        item
        for item in adapter_doc.get("classifier_questions", [])
        if isinstance(item, dict) and item.get("surface_id") == surface_id
    ]
    if len(questions) != 1:
        raise SemanticRuntimeError(f"surface {surface_id} must map to exactly one classifier question")
    action_question = questions[0]
    if action_question.get("type", "choice") != "choice":
        raise SemanticRuntimeError("semantic runtime requires a Choice for final action selection")
    if action_question.get("criteria_source", "static") != "static":
        raise SemanticRuntimeError("semantic runtime currently requires a static final action Choice")

    actions = _by_id(actions_doc.get("actions"), "action_id")
    legal = _legal_actions(actions, surface, current_state, state)
    exposed = [
        item
        for item in action_question.get("choices", [])
        if isinstance(item, dict) and item.get("executor_action_id") in legal
    ]
    fallback_choice = action_question.get("abstention_choice")
    fallback_item = next((item for item in action_question.get("choices", []) if item.get("id") == fallback_choice), None)
    fallback_action = fallback_item.get("executor_action_id") if fallback_item else None
    base = {
        "schema_version": "2.0",
        "system_id": system_id,
        "surface_id": surface_id,
        "state_id": current_state,
        "question_id": action_question["question_id"],
        "legal_action_ids": sorted(legal),
        "exposed_choice_ids": [item["id"] for item in exposed],
        "judgment_ids": list(judgment_questions),
        "judgment_request": judgment_request,
    }
    if not exposed:
        return {**base, "status": "no_legal_action", "reason": "no surface action passed deterministic preconditions"}
    if fallback_action not in legal:
        return {**base, "status": "no_safe_fallback", "reason": "the declared abstention action is not legal in this state"}

    unique_actions = {item["executor_action_id"] for item in exposed}
    if len(unique_actions) == 1:
        action_id = next(iter(unique_actions))
        choice_id = next(item["id"] for item in exposed if item["executor_action_id"] == action_id)
        return {**base, "status": "deterministic", "action_id": action_id, "selected_choice": choice_id}

    criteria = {item["id"]: item.get("criterion") or item["id"] for item in exposed}
    action_request = {
        "model": model,
        "state": {
            "context": context,
            "runtime_state": dict(state),
            "semantic_judgments": "$stage_1.answers",
            "allowed_actions": sorted(legal),
            "system_id": system_id,
        },
        "questions": {
            action_question["question_id"]: {
                "type": "choice",
                "instructions": action_question.get("instruction")
                or action_question.get("instructions")
                or "Choose the best supported legal action.",
                "criteria": criteria,
            }
        },
    }
    return {**base, "status": "ready", "action_request": action_request}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_judgment_response(
    request: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    answers = response.get("answers")
    if not isinstance(answers, Mapping):
        raise SemanticRuntimeError("JEV judgment response omitted answers")
    validated: dict[str, dict[str, Any]] = {}
    for question_id, question in (request.get("questions") or {}).items():
        answer = answers.get(question_id)
        if not isinstance(answer, Mapping):
            raise SemanticRuntimeError(f"JEV judgment response omitted {question_id!r}")
        kind = question.get("type")
        if kind == "choice":
            if answer.get("choice") not in (question.get("criteria") or {}):
                raise SemanticRuntimeError(f"JEV returned an illegal choice for judgment {question_id}")
        elif kind == "noul":
            value = _number(answer.get("noul"))
            if value is None or not 0.0 <= value <= 1.0:
                raise SemanticRuntimeError(f"JEV returned an invalid Noul for judgment {question_id}")
        elif kind == "score":
            value = _number(answer.get("score"))
            levels = question.get("criteria") or []
            if value is None or not 0.0 <= value <= max(0, len(levels) - 1):
                raise SemanticRuntimeError(f"JEV returned an invalid Score for judgment {question_id}")
        validated[question_id] = dict(answer)
    return validated


class JudgmentLog:
    """Append supporting judgments in the raw format accepted by jev_gate.py."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append_response(self, *, case_id: str | None, response: Mapping[str, Any]) -> None:
        answers = response.get("answers") or {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for question_id, answer in answers.items():
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "case_id": case_id,
                    "question_id": question_id,
                    "jev": answer,
                    "model": response.get("model"),
                    "usage": response.get("usage"),
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(
    adapter: Any,
    bundle: str | Path,
    context: Any,
    state: Mapping[str, Any],
    *,
    surface_id: str | None = None,
    case_id: str | None = None,
    transport: Transport | None = None,
    judgment_log: JudgmentLog | None = None,
    log: Any = None,
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None,
    guard: Callable[[str, Mapping[str, Any]], bool] | None = None,
    confirmed: bool = False,
) -> tuple[Any, Any, dict[str, dict[str, Any]]]:
    """Evaluate supporting judgments, select among legal actions, then execute via the generated adapter."""
    plan = compile_plan(bundle, context, state, surface_id=surface_id)
    if plan["status"] in {"no_legal_action", "no_safe_fallback"}:
        raise SemanticRuntimeError(plan["reason"])
    send = transport or adapter.default_transport()
    judgments: dict[str, dict[str, Any]] = {}
    judgment_request = plan["judgment_request"]
    if judgment_request["questions"]:
        response = send(judgment_request)
        if not isinstance(response, Mapping):
            raise SemanticRuntimeError("judgment transport returned no response mapping")
        judgments = validate_judgment_response(judgment_request, response)
        if judgment_log is not None:
            judgment_log.append_response(case_id=case_id, response=response)

    if plan["status"] == "deterministic":
        decision = adapter.Decision(
            question_id=plan["question_id"],
            action_id=plan["action_id"],
            confidence=None,
            probabilities={},
            selected_choice=plan["selected_choice"],
            proposed_action_id=plan["action_id"],
            reason="deterministic:one_legal_action",
            decided_by="deterministic",
            case_id=case_id,
        )
    else:
        payload = json.loads(json.dumps(plan["action_request"]))
        payload["state"]["semantic_judgments"] = judgments
        started = time.perf_counter()
        response = send(payload)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if not isinstance(response, Mapping):
            raise SemanticRuntimeError("action transport returned no response mapping")
        raw = (response.get("answers") or {}).get(plan["question_id"]) or {}
        if raw.get("choice") not in plan["exposed_choice_ids"]:
            raise adapter.IllegalChoice(
                f"JEV returned choice {raw.get('choice')!r} outside the exposed legal set"
            )
        decision = adapter.parse_response(response, plan["question_id"])
        decision = adapter.apply_policy(
            replace(decision, latency_ms=latency_ms, case_id=case_id)
        )
    if decision.action_id not in plan["legal_action_ids"]:
        raise adapter.ExecutionBlocked(
            f"policy resolved to action {decision.action_id!r}, outside the legal runtime set"
        )
    result = adapter.execute(
        decision,
        state,
        handlers=handlers,
        guard=guard,
        confirmed=confirmed,
        log=log,
        case_id=case_id,
    )
    return decision, result, judgments


def _read_json(value: str | None, path: Path | None) -> Any:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value or "{}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--state", help="runtime state as JSON")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--context", help="semantic context as JSON")
    parser.add_argument("--context-file", type=Path)
    parser.add_argument("--surface")
    args = parser.parse_args(argv)
    state = _read_json(args.state, args.state_file)
    context = _read_json(args.context, args.context_file)
    print(json.dumps(compile_plan(args.bundle, context, state, surface_id=args.surface), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
