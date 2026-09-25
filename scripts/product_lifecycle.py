#!/usr/bin/env python3
"""Validate and interpret the canonical Decision System Forge lifecycle.

The YAML contract owns product-facing stage names, persisted next-action
vocabulary, legal transitions, and safe handoff guidance. Compiler modules still
own their domain artifacts; this module prevents those outputs from drifting
away from the public product specification.
"""

from __future__ import annotations

import copy
import shlex
from pathlib import Path
from typing import Any, Mapping

import yaml


REQUIRED_SAFETY = {
    "semantic_judgment_never_authorizes_action",
    "no_invented_bindings",
    "no_auto_approval",
    "zero_production_authority",
    "no_secrets_or_raw_private_state_in_receipts",
}
REQUIRED_PUBLIC_COMMANDS = {"start", "inspect", "continue", "status"}
GUIDANCE_FIELDS = {"action_id", "summary", "required_inputs", "command", "automatic"}


class LifecycleError(ValueError):
    """The canonical lifecycle or a persisted project violates its contract."""


def load_contract(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LifecycleError(f"cannot read product lifecycle {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError("product lifecycle must contain a mapping")
    errors = validate_contract(value)
    if errors:
        raise LifecycleError(errors[0])
    return value


def _strings(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def validate_contract(contract: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") != "1.0":
        errors.append("product lifecycle schema_version must be '1.0'")
    if contract.get("name") != "decision-system-forge":
        errors.append("product lifecycle name must be 'decision-system-forge'")
    public_commands = contract.get("public_commands")
    if not isinstance(public_commands, Mapping) or set(public_commands) != REQUIRED_PUBLIC_COMMANDS:
        errors.append("public_commands must declare start, inspect, continue, and status exactly")
    safety = contract.get("safety")
    if not _strings(safety) or set(safety) != REQUIRED_SAFETY:
        missing = sorted(REQUIRED_SAFETY - set(safety or [])) if isinstance(safety, list) else sorted(REQUIRED_SAFETY)
        errors.append("safety must preserve all required boundaries; missing: " + ", ".join(missing))

    stages = contract.get("stages")
    if not isinstance(stages, Mapping) or not stages:
        return errors + ["product lifecycle stages must be a non-empty mapping"]
    stage_ids = set(stages)
    for stage_id, raw in stages.items():
        if not isinstance(stage_id, str) or not stage_id or not isinstance(raw, Mapping):
            errors.append(f"invalid stage definition for {stage_id!r}")
            continue
        next_actions = raw.get("manifest_next_actions")
        if not _strings(next_actions):
            errors.append(f"stage {stage_id}: manifest_next_actions must be non-empty strings")
        transitions = raw.get("transitions")
        if not isinstance(transitions, list):
            errors.append(f"stage {stage_id}: transitions must be a list")
        else:
            for transition in transitions:
                if not isinstance(transition, Mapping) or set(transition) != {"to", "via"}:
                    errors.append(f"stage {stage_id}: each transition requires only to and via")
                    continue
                if transition.get("to") not in stage_ids:
                    errors.append(f"stage {stage_id}: transition targets missing_stage {transition.get('to')!r}")
                if not isinstance(transition.get("via"), str) or not transition.get("via"):
                    errors.append(f"stage {stage_id}: transition via must be a non-empty string")
        guidance = raw.get("guidance")
        if not isinstance(guidance, Mapping) or set(guidance) != GUIDANCE_FIELDS:
            errors.append(f"stage {stage_id}: guidance must contain exactly {sorted(GUIDANCE_FIELDS)}")
            continue
        if guidance.get("action_id") not in (next_actions or []):
            errors.append(f"stage {stage_id}: guidance action_id must be a persisted next action")
        if not isinstance(guidance.get("summary"), str) or not guidance.get("summary"):
            errors.append(f"stage {stage_id}: guidance summary must be non-empty")
        if not _strings(guidance.get("required_inputs")):
            errors.append(f"stage {stage_id}: guidance required_inputs must be a string list")
        command = guidance.get("command")
        if command is not None:
            remainder = command.replace("{project}", "") if isinstance(command, str) else "{}"
            if not isinstance(command, str) or "{" in remainder or "}" in remainder:
                errors.append(f"stage {stage_id}: guidance command may use only the {{project}} placeholder")
        if not isinstance(guidance.get("automatic"), bool):
            errors.append(f"stage {stage_id}: guidance automatic must be boolean")

    entries = contract.get("entry_paths")
    if not isinstance(entries, Mapping) or not entries:
        errors.append("entry_paths must be a non-empty mapping")
    else:
        for entry_id, entry in entries.items():
            if not isinstance(entry, Mapping):
                errors.append(f"entry path {entry_id}: definition must be a mapping")
                continue
            initial = entry.get("initial_stages")
            if not _strings(initial) or not set(initial) <= stage_ids:
                errors.append(f"entry path {entry_id}: initial_stages must reference known stages")
            command = entry.get("command")
            if not isinstance(command, str) or command not in (set(public_commands or {}) | {"scan"}):
                errors.append(f"entry path {entry_id}: command is not declared")
            if not isinstance(entry.get("route"), str) or not entry.get("route"):
                errors.append(f"entry path {entry_id}: route must be non-empty")
    return errors


def validate_manifest(contract: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    stages = contract.get("stages") or {}
    stage = manifest.get("stage")
    if stage not in stages:
        return [f"project stage {stage!r} is absent from the canonical lifecycle"]
    next_action = manifest.get("next_action")
    allowed = stages[stage].get("manifest_next_actions") or []
    if next_action not in allowed:
        errors.append(
            f"project next_action {next_action!r} is invalid for lifecycle stage {stage!r}; allowed={allowed}"
        )
    safety = manifest.get("safety")
    if isinstance(safety, Mapping):
        if safety.get("production_authority") is True:
            errors.append("project manifest cannot grant production authority")
        if safety.get("executes_actions") is True:
            errors.append("product lifecycle project cannot execute actions")
    return errors


def transition_allowed(contract: Mapping[str, Any], before: str, after: str, via: str) -> bool:
    stage = (contract.get("stages") or {}).get(before) or {}
    return any(
        isinstance(item, Mapping) and item.get("to") == after and item.get("via") == via
        for item in stage.get("transitions") or []
    )


def guidance_for(
    contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
    project: str | Path,
) -> dict[str, Any]:
    errors = validate_manifest(contract, manifest)
    if errors:
        raise LifecycleError(errors[0])
    definition = contract["stages"][manifest["stage"]]
    guidance = copy.deepcopy(definition["guidance"])
    command = guidance.get("command")
    if command is not None:
        guidance["command"] = command.format(project=shlex.quote(str(Path(project).expanduser().resolve())))
    guidance["stage"] = manifest["stage"]
    guidance["persisted_next_action"] = manifest["next_action"]
    return guidance


__all__ = [
    "LifecycleError",
    "REQUIRED_PUBLIC_COMMANDS",
    "REQUIRED_SAFETY",
    "guidance_for",
    "load_contract",
    "transition_allowed",
    "validate_contract",
    "validate_manifest",
]
