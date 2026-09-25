#!/usr/bin/env python3
"""Validate observable decision events and compile a descriptive workflow map.

The compiler keeps raw state and identifiers in memory only. Persisted outputs
contain source hashes, aggregate counts, and action paths; they are evidence of
observed recurrence, not approval for JEV or execution.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import scan_decision_opportunities as opportunity_scan


REQUIRED_FIELDS = (
    "event_id",
    "case_id",
    "opportunity_id",
    "occurred_at",
    "actor_type",
    "state_before",
    "available_actions",
    "selected_action",
    "state_after",
    "source_ref",
)
OPTIONAL_FIELDS = ("confidence", "eventual_outcome", "outcome_observed_at")
ALLOWED_EVENT_ACTORS = {"human", "agent", "software"}
FORBIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "private_reasoning",
    "reasoning_trace",
    "scratchpad",
}


class ObservationError(ValueError):
    """An event source violates the normalized observation contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ObservationError(f"cannot read event source {path}: {exc}") from exc
    return digest.hexdigest()


def _timestamp(value: Any, *, field: str, event_id: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ObservationError(f"event {event_id}: {field} must be a timezone-aware timestamp")
    rendered = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise ObservationError(f"event {event_id}: {field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObservationError(f"event {event_id}: {field} must include a timezone")
    return parsed


def _contains_private_reasoning(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if opportunity_scan.slug(key) in FORBIDDEN_REASONING_KEYS:
                return True
            if _contains_private_reasoning(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_private_reasoning(item) for item in value)
    return False


def load_events(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ObservationError(f"cannot read event source {source}: {exc}") from exc
    events = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ObservationError(f"event source line {line_number} is not valid JSON: {exc.msg}") from exc
        if not isinstance(item, dict):
            raise ObservationError(f"event source line {line_number} must contain an object")
        events.append(item)
    if not events:
        raise ObservationError("event source contains no events")
    return events


def validate_events(
    events: Iterable[Mapping[str, Any]],
    opportunity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    opportunity_id = str(opportunity.get("opportunity_id") or "")
    expected_actor = str(opportunity.get("actor_type") or "unknown")
    candidate_actions = {
        opportunity_scan.slug(item, fallback="action")
        for item in opportunity.get("candidate_actions", [])
        if isinstance(item, str) and item.strip()
    }
    if len(candidate_actions) < 2:
        raise ObservationError("selected opportunity does not have a bounded action vocabulary")

    normalized = []
    event_ids: set[str] = set()
    case_times: dict[str, set[datetime]] = defaultdict(set)
    allowed_fields = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    for index, raw in enumerate(events, 1):
        if not isinstance(raw, Mapping):
            raise ObservationError(f"event {index}: each event must be an object")
        missing = [field for field in REQUIRED_FIELDS if field not in raw]
        if missing:
            raise ObservationError(f"event {index}: missing required fields: {', '.join(missing)}")
        extra = sorted(set(raw) - allowed_fields)
        if extra:
            raise ObservationError(f"event {index}: unsupported fields: {', '.join(extra)}")

        event_id = raw.get("event_id")
        case_id = raw.get("case_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ObservationError(f"event {index}: event_id must be a non-empty string")
        if event_id in event_ids:
            raise ObservationError(f"event {event_id}: duplicate event_id")
        event_ids.add(event_id)
        if not isinstance(case_id, str) or not case_id.strip():
            raise ObservationError(f"event {event_id}: case_id must be a non-empty string")
        if raw.get("opportunity_id") != opportunity_id:
            raise ObservationError(f"event {event_id}: opportunity_id does not match the selected hypothesis")

        actor_type = raw.get("actor_type")
        if actor_type not in ALLOWED_EVENT_ACTORS:
            raise ObservationError(f"event {event_id}: unsupported actor_type {actor_type!r}")
        if expected_actor not in {"unknown", "mixed"} and actor_type != expected_actor:
            raise ObservationError(f"event {event_id}: actor_type does not match the selected hypothesis")

        occurred_at = _timestamp(raw.get("occurred_at"), field="occurred_at", event_id=event_id)
        if occurred_at in case_times[case_id]:
            raise ObservationError(f"case {case_id}: ambiguous event order at {raw.get('occurred_at')}")
        case_times[case_id].add(occurred_at)

        if not isinstance(raw.get("state_before"), Mapping) or not isinstance(raw.get("state_after"), Mapping):
            raise ObservationError(f"event {event_id}: state_before and state_after must be objects")
        if _contains_private_reasoning(raw.get("state_before")) or _contains_private_reasoning(raw.get("state_after")):
            raise ObservationError(f"event {event_id}: private reasoning and chain-of-thought are forbidden")

        available_raw = raw.get("available_actions")
        if not isinstance(available_raw, list) or not available_raw or any(
            not isinstance(item, str) or not item.strip() for item in available_raw
        ):
            raise ObservationError(f"event {event_id}: available_actions must be a non-empty string list")
        available = [opportunity_scan.slug(item, fallback="action") for item in available_raw]
        if len(set(available)) != len(available):
            raise ObservationError(f"event {event_id}: available_actions contains duplicates")
        unsupported = sorted(set(available) - candidate_actions)
        if unsupported:
            raise ObservationError(f"event {event_id}: available_actions contains unsupported actions {unsupported}")
        selected_raw = raw.get("selected_action")
        if not isinstance(selected_raw, str) or not selected_raw.strip():
            raise ObservationError(f"event {event_id}: selected_action must be a non-empty string")
        selected_action = opportunity_scan.slug(selected_raw, fallback="action")
        if selected_action not in candidate_actions or selected_action not in available:
            raise ObservationError(f"event {event_id}: selected_action is not an available candidate action")

        source_ref = raw.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise ObservationError(f"event {event_id}: source_ref must be a non-empty string")
        confidence = raw.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ObservationError(f"event {event_id}: confidence must be between 0 and 1")
        outcome_time = None
        if raw.get("outcome_observed_at") is not None:
            outcome_time = _timestamp(
                raw.get("outcome_observed_at"),
                field="outcome_observed_at",
                event_id=event_id,
            )
            if outcome_time < occurred_at:
                raise ObservationError(f"event {event_id}: outcome_observed_at precedes occurred_at")
        if _contains_private_reasoning(raw.get("eventual_outcome")):
            raise ObservationError(f"event {event_id}: private reasoning and chain-of-thought are forbidden")

        normalized.append(
            {
                "event_id": event_id,
                "case_id": case_id,
                "opportunity_id": opportunity_id,
                "occurred_at": occurred_at,
                "actor_type": actor_type,
                "selected_action": selected_action,
            }
        )
    return normalized


def event_schema(opportunity: Mapping[str, Any]) -> dict[str, Any]:
    opportunity_id = str(opportunity["opportunity_id"])
    actor_type = str(opportunity.get("actor_type") or "unknown")
    actors = sorted(ALLOWED_EVENT_ACTORS) if actor_type in {"unknown", "mixed"} else [actor_type]
    actions = sorted(
        {
            opportunity_scan.slug(item, fallback="action")
            for item in opportunity.get("candidate_actions", [])
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"Decision observation: {opportunity_id}",
        "type": "object",
        "additionalProperties": False,
        "required": list(REQUIRED_FIELDS),
        "properties": {
            "event_id": {"type": "string", "minLength": 1},
            "case_id": {"type": "string", "minLength": 1},
            "opportunity_id": {"const": opportunity_id},
            "occurred_at": {"type": "string", "format": "date-time"},
            "actor_type": {"enum": actors},
            "state_before": {"type": "object"},
            "available_actions": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"enum": actions},
            },
            "selected_action": {"enum": actions},
            "state_after": {"type": "object"},
            "source_ref": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "eventual_outcome": {},
            "outcome_observed_at": {"type": "string", "format": "date-time"},
        },
    }


def compile_decision_system_map(
    events: list[dict[str, Any]],
    opportunity: Mapping[str, Any],
    *,
    system_id: str,
    minimum_events: int = 3,
    minimum_path_cases: int = 2,
) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in events:
        by_case[item["case_id"]].append(item)

    signatures: Counter[tuple[tuple[str, str], ...]] = Counter()
    actor_sets: dict[tuple[tuple[str, str], ...], set[str]] = defaultdict(set)
    transitions: Counter[tuple[str, str, str]] = Counter()
    terminal_actions: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    for case_events in by_case.values():
        ordered = sorted(case_events, key=lambda item: item["occurred_at"])
        signature = tuple((item["opportunity_id"], item["selected_action"]) for item in ordered)
        signatures[signature] += 1
        actor_sets[signature].update(item["actor_type"] for item in ordered)
        action_counts.update(item["selected_action"] for item in ordered)
        terminal_actions[ordered[-1]["selected_action"]] += 1
        for current, following in zip(ordered, ordered[1:]):
            transitions[
                (current["opportunity_id"], current["selected_action"], following["opportunity_id"])
            ] += 1

    observed_paths = []
    for signature, case_count in sorted(
        signatures.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        encoded = json.dumps(signature, separators=(",", ":"), ensure_ascii=True)
        observed_paths.append(
            {
                "path_id": "path:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16],
                "case_count": case_count,
                "event_count": len(signature) * case_count,
                "repeated": case_count >= minimum_path_cases,
                "actor_types": sorted(actor_sets[signature]),
                "steps": [
                    {"opportunity_id": opportunity_id, "selected_action": selected_action}
                    for opportunity_id, selected_action in signature
                ],
            }
        )

    repeated_paths = sum(item["repeated"] for item in observed_paths)
    return {
        "schema_version": "1.0",
        "system_id": system_id,
        "mode": "observed_workflow_map",
        "nodes": [
            {
                "opportunity_id": opportunity["opportunity_id"],
                "actor_type": opportunity.get("actor_type", "unknown"),
                "candidate_actions": sorted(opportunity.get("candidate_actions", [])),
                "observed_events": len(events),
                "observed_cases": len(by_case),
                "action_counts": dict(sorted(action_counts.items())),
            }
        ],
        "transitions": [
            {
                "from_opportunity": source,
                "via_action": action,
                "to_opportunity": destination,
                "count": count,
            }
            for (source, action, destination), count in sorted(transitions.items())
        ],
        "terminal_actions": dict(sorted(terminal_actions.items())),
        "observed_paths": observed_paths,
        "readiness": {
            "minimum_events": minimum_events,
            "minimum_path_cases": minimum_path_cases,
            "repeated_paths": repeated_paths,
            "workflow_map_ready": len(events) >= minimum_events and repeated_paths > 0,
        },
        "safety": {
            "descriptive_only": True,
            "raw_event_state_persisted": False,
            "case_identifiers_persisted": False,
            "shadow_eligible": False,
            "executes_actions": False,
        },
    }


def observe(
    event_source: str | Path,
    opportunity: Mapping[str, Any],
    *,
    system_id: str,
    minimum_events: int = 3,
    minimum_path_cases: int = 2,
) -> dict[str, Any]:
    path = Path(event_source).expanduser().resolve()
    if minimum_events < 1 or minimum_path_cases < 2:
        raise ObservationError("minimum_events must be positive and minimum_path_cases must be at least 2")
    digest_before = sha256_file(path)
    loaded = load_events(path)
    digest_after = sha256_file(path)
    if digest_before != digest_after:
        raise ObservationError("event source changed while it was being validated")
    events = validate_events(loaded, opportunity)
    decision_map = compile_decision_system_map(
        events,
        opportunity,
        system_id=system_id,
        minimum_events=minimum_events,
        minimum_path_cases=minimum_path_cases,
    )
    return {
        "decision_system_map": decision_map,
        "observation_manifest": {
            "schema_version": "1.0",
            "system_id": system_id,
            "opportunity_id": opportunity["opportunity_id"],
            "event_source": {
                "locator": str(path),
                "sha256": digest_after,
                "format": "jsonl",
            },
            "summary": {
                "events": len(events),
                "cases": len({item["case_id"] for item in events}),
                "repeated_paths": decision_map["readiness"]["repeated_paths"],
            },
            "privacy": {
                "raw_events_copied": False,
                "raw_state_persisted": False,
                "event_ids_persisted": False,
                "case_ids_persisted": False,
                "chain_of_thought_allowed": False,
            },
        },
    }
