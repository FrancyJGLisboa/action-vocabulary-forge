#!/usr/bin/env python3
"""Validate binding proposals and externally produced runtime evidence.

The Forge does not import, invoke, or otherwise execute a proposed target.
It compiles typed candidates, validates privacy-minimized observations emitted
by a controlled host, and can promote a candidate to *shadow-only* evidence.
Production authority and executable bindings remain explicitly false.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

import yaml

import integration_package


BINDING_KINDS = {"python_callable", "http", "cli", "mcp", "ui"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
UI_OPERATIONS = {"goto", "click", "fill", "select", "press", "check", "uncheck", "read"}
RESULT_STATUSES = {"succeeded", "rejected", "failed"}
CASE_TYPES = {"success", "negative"}
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
PYTHON_TARGET = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s|api[_-]?key|password|secret|access[_-]?token|authorization)"
)
SHELL_PATTERN = re.compile(r"[;&|><`$\n\r]")
SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}

OBSERVATION_FIELDS = {
    "observation_id",
    "binding_id",
    "binding_digest",
    "action_id",
    "test_run_id",
    "occurred_at",
    "environment_id",
    "case_type",
    "state_before_fingerprint",
    "invocation_fingerprint",
    "result_status",
    "state_after_fingerprint",
    "postcondition_id",
    "postcondition_met",
    "source_ref",
    "harness_id",
    "harness_sha256",
}


class BindingVerificationError(ValueError):
    """A binding proposal or observation violates the trust boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BindingVerificationError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _mapping_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_digest(value: Mapping[str, Any]) -> str:
    return _mapping_digest({key: item for key, item in value.items() if key != "binding_digest"})


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BindingVerificationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BindingVerificationError(f"{path} must contain a mapping")
    return value


def _write_yaml(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(SECRET_PATTERN.search(str(key)) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and SECRET_PATTERN.search(value) is not None


def _string_list(value: Any, *, owner: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise BindingVerificationError(f"{owner} must be a non-empty string list")
    return [item.strip() for item in value]


def _validate_identifier(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise BindingVerificationError(f"{field} must be a stable identifier")
    return value


def _validate_locator(kind: str, value: Any, *, binding_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BindingVerificationError(f"binding {binding_id}: locator must be a mapping")
    locator = dict(value)
    if _contains_secret(locator):
        raise BindingVerificationError(f"binding {binding_id}: locator contains a secret or credential field")

    if kind == "python_callable":
        if set(locator) != {"target"} or not isinstance(locator.get("target"), str) or not PYTHON_TARGET.fullmatch(locator["target"]):
            raise BindingVerificationError(
                f"binding {binding_id}: python_callable locator requires only target='module:function'"
            )
    elif kind == "http":
        if set(locator) != {"url", "method"}:
            raise BindingVerificationError(f"binding {binding_id}: http locator requires only url and method")
        url = locator.get("url")
        method = locator.get("method")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or method not in HTTP_METHODS
        ):
            raise BindingVerificationError(f"binding {binding_id}: http locator needs an HTTPS URL and supported method")
    elif kind == "cli":
        if set(locator) != {"argv"}:
            raise BindingVerificationError(f"binding {binding_id}: cli locator requires only argv")
        argv = locator.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item or SHELL_PATTERN.search(item) for item in argv)
            or Path(argv[0]).name.lower() in SHELL_EXECUTABLES
        ):
            raise BindingVerificationError(
                f"binding {binding_id}: argv must be a non-empty argument list without a shell or shell metacharacters"
            )
    elif kind == "mcp":
        if set(locator) != {"server", "tool"} or any(
            not isinstance(locator.get(field), str) or not locator[field].strip()
            for field in ("server", "tool")
        ):
            raise BindingVerificationError(f"binding {binding_id}: mcp locator requires only server and tool")
    elif kind == "ui":
        operation = locator.get("operation")
        if operation not in UI_OPERATIONS:
            raise BindingVerificationError(f"binding {binding_id}: ui locator has an unsupported operation")
        if operation == "goto":
            if set(locator) != {"operation", "url"}:
                raise BindingVerificationError(f"binding {binding_id}: ui goto locator requires only operation and url")
            parsed = urlsplit(locator.get("url")) if isinstance(locator.get("url"), str) else None
            if parsed is None or parsed.scheme != "https" or not parsed.netloc:
                raise BindingVerificationError(f"binding {binding_id}: ui goto requires an HTTPS URL")
        elif set(locator) != {"operation", "selector"} or not isinstance(locator.get("selector"), str) or not locator["selector"].strip():
            raise BindingVerificationError(
                f"binding {binding_id}: ui {operation} locator requires only operation and selector"
            )
    return locator


def _resolve_evidence_sources(project: Path, project_manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    evidence = project_manifest.get("evidence_manifest")
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("locator"), str):
        raise BindingVerificationError("project evidence manifest provenance is missing")
    manifest_path = Path(evidence["locator"]).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = project / manifest_path
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file() or _sha256(manifest_path) != evidence.get("sha256"):
        raise BindingVerificationError("project evidence manifest hash mismatch")
    document = _load_yaml(manifest_path)
    system = next(
        (
            item
            for item in document.get("systems", [])
            if isinstance(item, Mapping) and item.get("system_id") == project_manifest.get("system_id")
        ),
        None,
    )
    if system is None:
        raise BindingVerificationError("project system is absent from its evidence manifest")
    result: dict[str, dict[str, Any]] = {}
    for source in system.get("sources", []):
        if not isinstance(source, Mapping) or not isinstance(source.get("source_id"), str):
            raise BindingVerificationError("evidence manifest contains an invalid source")
        path = (manifest_path.parent / str(source.get("file", ""))).resolve()
        if not path.is_file() or _sha256(path) != source.get("sha256"):
            raise BindingVerificationError(f"source {source.get('source_id')}: source hash mismatch")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BindingVerificationError(f"source {source.get('source_id')}: cannot read evidence text") from exc
        result[str(source["source_id"])] = {**dict(source), "path": path, "text": text}
    return result


def resolve_evidence_sources(project: str | Path, project_manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Load and hash-check the exact evidence sources attached to a Forge project."""
    return _resolve_evidence_sources(Path(project).expanduser().resolve(), project_manifest)


def _observation_schema(binding_ids: list[str], action_ids: list[str]) -> dict[str, Any]:
    fingerprint = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Forge binding runtime observation",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(OBSERVATION_FIELDS),
        "properties": {
            "observation_id": {"type": "string", "minLength": 1},
            "binding_id": {"enum": sorted(binding_ids)},
            "binding_digest": fingerprint,
            "action_id": {"enum": sorted(action_ids)},
            "test_run_id": {"type": "string", "minLength": 1},
            "occurred_at": {"type": "string", "format": "date-time"},
            "environment_id": {"type": "string", "minLength": 1},
            "case_type": {"enum": sorted(CASE_TYPES)},
            "state_before_fingerprint": fingerprint,
            "invocation_fingerprint": fingerprint,
            "result_status": {"enum": sorted(RESULT_STATUSES)},
            "state_after_fingerprint": fingerprint,
            "postcondition_id": {"type": "string", "minLength": 1},
            "postcondition_met": {"const": True},
            "source_ref": {"type": "string", "minLength": 1},
            "harness_id": {"type": "string", "minLength": 1},
            "harness_sha256": fingerprint,
        },
    }


def propose_bindings(*, project: str | Path, proposal: str | Path) -> dict[str, Any]:
    """Compile an agent-authored proposal without importing or invoking targets."""
    root = Path(project).expanduser().resolve()
    project_file = root / "forge_project.yaml"
    project_manifest = _load_yaml(project_file)
    if project_manifest.get("stage") != "integration_planned":
        raise BindingVerificationError(
            f"binding proposals require stage 'integration_planned', got {project_manifest.get('stage')!r}"
        )
    package_errors = integration_package.verify_package(root)
    if package_errors:
        raise BindingVerificationError(f"integration package is invalid: {package_errors[0]}")

    proposal_path = Path(proposal).expanduser().resolve()
    document = _load_yaml(proposal_path)
    if document.get("schema_version") != "1.0" or document.get("system_id") != project_manifest.get("system_id"):
        raise BindingVerificationError("binding proposal identity does not match the Forge project")
    raw_bindings = document.get("bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise BindingVerificationError("binding proposal must contain a non-empty bindings list")

    registry_path = root / "integration" / "action_registry.yaml"
    registry = _load_yaml(registry_path)
    known_actions = {
        item.get("action_id"): item
        for item in registry.get("actions", [])
        if isinstance(item, Mapping) and isinstance(item.get("action_id"), str)
    }
    sources = _resolve_evidence_sources(root, project_manifest)
    bindings: list[dict[str, Any]] = []
    binding_ids: set[str] = set()
    action_ids: set[str] = set()
    allowed_fields = {
        "binding_id",
        "action_id",
        "kind",
        "locator",
        "preconditions",
        "success_postconditions",
        "failure_postconditions",
        "evidence",
    }
    for index, raw in enumerate(raw_bindings, 1):
        if not isinstance(raw, Mapping):
            raise BindingVerificationError(f"binding proposal entry {index} must be a mapping")
        extra = sorted(set(raw) - allowed_fields)
        if extra:
            raise BindingVerificationError(f"binding proposal entry {index}: unsupported fields: {', '.join(extra)}")
        if _contains_secret(raw):
            raise BindingVerificationError(f"binding proposal entry {index}: proposal contains a secret or credential")
        binding_id = _validate_identifier(raw.get("binding_id"), field=f"binding proposal entry {index} binding_id")
        action_id = _validate_identifier(raw.get("action_id"), field=f"binding {binding_id} action_id")
        if binding_id in binding_ids:
            raise BindingVerificationError(f"duplicate binding_id {binding_id!r}")
        if action_id in action_ids:
            raise BindingVerificationError(f"action {action_id}: more than one candidate binding was proposed")
        if action_id not in known_actions:
            raise BindingVerificationError(f"binding {binding_id}: unknown action_id {action_id!r}")
        if (known_actions[action_id].get("binding") or {}).get("status") != "stub":
            raise BindingVerificationError(f"action {action_id}: binding is not an unverified stub")
        kind = raw.get("kind")
        if kind not in BINDING_KINDS:
            raise BindingVerificationError(f"binding {binding_id}: unsupported kind {kind!r}")
        locator = _validate_locator(str(kind), raw.get("locator"), binding_id=binding_id)
        preconditions = _string_list(raw.get("preconditions"), owner=f"binding {binding_id} preconditions")
        success = _string_list(
            raw.get("success_postconditions"), owner=f"binding {binding_id} success_postconditions"
        )
        failure = _string_list(
            raw.get("failure_postconditions"), owner=f"binding {binding_id} failure_postconditions"
        )
        evidence = raw.get("evidence")
        if not isinstance(evidence, Mapping) or set(evidence) != {"source_id", "source_sha256", "quote"}:
            raise BindingVerificationError(
                f"binding {binding_id}: evidence requires only source_id, source_sha256, and quote"
            )
        source_id = evidence.get("source_id")
        source = sources.get(source_id) if isinstance(source_id, str) else None
        if source is None:
            raise BindingVerificationError(f"binding {binding_id}: unknown evidence source {source_id!r}")
        if evidence.get("source_sha256") != source.get("sha256"):
            raise BindingVerificationError(f"binding {binding_id}: source_sha256 does not match inventoried evidence")
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote.strip() or quote not in source["text"]:
            raise BindingVerificationError(f"binding {binding_id}: quote is not present in source {source_id!r}")

        candidate: dict[str, Any] = {
            "binding_id": binding_id,
            "action_id": action_id,
            "action_label": known_actions[action_id].get("label"),
            "status": "candidate",
            "kind": kind,
            "locator": locator,
            "preconditions": [
                {"precondition_id": f"precondition_{offset}", "description": item}
                for offset, item in enumerate(preconditions, 1)
            ],
            "postconditions": [
                *(
                    {"postcondition_id": f"success_{offset}", "case_type": "success", "description": item}
                    for offset, item in enumerate(success, 1)
                ),
                *(
                    {"postcondition_id": f"failure_{offset}", "case_type": "negative", "description": item}
                    for offset, item in enumerate(failure, 1)
                ),
            ],
            "evidence": {
                "source_id": source_id,
                "source_sha256": source["sha256"],
                "quote": quote,
            },
            "evidence_grade": "documented",
            "executable": False,
            "production_authority": False,
            "authority": "none",
        }
        candidate["binding_digest"] = _candidate_digest(candidate)
        bindings.append(candidate)
        binding_ids.add(binding_id)
        action_ids.add(action_id)

    output = {
        "schema_version": "1.0",
        "system_id": project_manifest["system_id"],
        "stage": "binding_candidates_ready",
        "bindings": bindings,
        "safety": {
            "proposal_is_authority": False,
            "executes_actions": False,
            "production_authority": False,
            "bindings_executable": False,
        },
    }
    package = root / "integration"
    candidates_path = package / "binding_candidates.yaml"
    schema_path = package / "binding_observation_schema.json"
    manifest_path = package / "binding_manifest.yaml"
    if any(path.exists() for path in (candidates_path, schema_path, manifest_path)):
        raise BindingVerificationError("binding candidate artifacts already exist")
    _write_yaml(candidates_path, output)
    _write_json(schema_path, _observation_schema(sorted(binding_ids), sorted(action_ids)))
    binding_manifest = {
        "schema_version": "1.0",
        "system_id": project_manifest["system_id"],
        "stage": "binding_candidates_ready",
        "created_at": _now(),
        "proposal_source": {"locator": str(proposal_path), "sha256": _sha256(proposal_path)},
        "artifacts": {
            "binding_candidates": {
                "file": "integration/binding_candidates.yaml",
                "sha256": _sha256(candidates_path),
            },
            "binding_observation_schema": {
                "file": "integration/binding_observation_schema.json",
                "sha256": _sha256(schema_path),
            },
        },
        "safety": {
            "forge_executes_target": False,
            "executes_actions": False,
            "production_authority": False,
            "bindings_executable": False,
        },
        "next_action": "run_candidates_in_controlled_host_and_emit_binding_observations",
    }
    _write_yaml(manifest_path, binding_manifest)
    project_manifest["stage"] = "binding_candidates_ready"
    project_manifest["updated_at"] = _now()
    project_manifest["next_action"] = "run_candidates_in_controlled_host_and_emit_binding_observations"
    project_manifest.setdefault("artifacts", {})["binding_manifest"] = "integration/binding_manifest.yaml"
    project_manifest.setdefault("safety", {}).update(
        {"executes_actions": False, "production_authority": False, "shadow_eligible": False}
    )
    _write_yaml(project_file, project_manifest)
    return binding_manifest


def _load_observations(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise BindingVerificationError(f"cannot read binding observations: {exc}") from exc
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BindingVerificationError(f"binding observation line {line_number} is invalid JSON") from exc
        if not isinstance(item, dict):
            raise BindingVerificationError(f"binding observation line {line_number} must be an object")
        result.append(item)
    if not result:
        raise BindingVerificationError("binding observation source contains no observations")
    return result


def _timestamp(value: Any, *, observation_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BindingVerificationError(f"observation {observation_id}: occurred_at must be timezone-aware")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise BindingVerificationError(f"observation {observation_id}: occurred_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BindingVerificationError(f"observation {observation_id}: occurred_at must be timezone-aware")
    return value.strip()


def _validate_observations(
    raw_observations: Iterable[Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    observation_ids: set[str] = set()
    for index, raw in enumerate(raw_observations, 1):
        if not isinstance(raw, Mapping):
            raise BindingVerificationError(f"observation {index}: must be an object")
        missing = sorted(OBSERVATION_FIELDS - set(raw))
        extra = sorted(set(raw) - OBSERVATION_FIELDS)
        if missing:
            raise BindingVerificationError(f"observation {index}: missing required fields: {', '.join(missing)}")
        if extra:
            raise BindingVerificationError(f"observation {index}: unsupported fields: {', '.join(extra)}")
        if _contains_secret(raw):
            raise BindingVerificationError(f"observation {index}: contains a secret or credential")
        observation_id = _validate_identifier(raw.get("observation_id"), field=f"observation {index} observation_id")
        if observation_id in observation_ids:
            raise BindingVerificationError(f"observation {observation_id}: duplicate observation_id")
        observation_ids.add(observation_id)
        binding_id = raw.get("binding_id")
        candidate = candidates.get(binding_id) if isinstance(binding_id, str) else None
        if candidate is None:
            raise BindingVerificationError(f"observation {observation_id}: unknown binding_id {binding_id!r}")
        if candidate.get("binding_digest") != _candidate_digest(candidate):
            raise BindingVerificationError(f"binding {binding_id}: binding_digest does not match candidate content")
        if raw.get("binding_digest") != candidate.get("binding_digest"):
            raise BindingVerificationError(f"observation {observation_id}: binding_digest does not match candidate")
        if raw.get("action_id") != candidate.get("action_id"):
            raise BindingVerificationError(f"observation {observation_id}: action_id does not match candidate")
        for field in ("test_run_id", "environment_id", "source_ref", "harness_id"):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                raise BindingVerificationError(f"observation {observation_id}: {field} must be non-empty")
        occurred_at = _timestamp(raw.get("occurred_at"), observation_id=observation_id)
        for field in (
            "state_before_fingerprint",
            "invocation_fingerprint",
            "state_after_fingerprint",
            "harness_sha256",
        ):
            if not isinstance(raw.get(field), str) or not FINGERPRINT.fullmatch(raw[field]):
                raise BindingVerificationError(f"observation {observation_id}: {field} must be a SHA-256 digest")
        case_type = raw.get("case_type")
        result_status = raw.get("result_status")
        if case_type not in CASE_TYPES or result_status not in RESULT_STATUSES:
            raise BindingVerificationError(f"observation {observation_id}: invalid case_type or result_status")
        postconditions = {
            item.get("postcondition_id"): item.get("case_type")
            for item in candidate.get("postconditions", [])
            if isinstance(item, Mapping)
        }
        postcondition_id = raw.get("postcondition_id")
        if postconditions.get(postcondition_id) != case_type:
            raise BindingVerificationError(
                f"observation {observation_id}: postcondition_id does not match the declared {case_type} case"
            )
        if raw.get("postcondition_met") is not True:
            raise BindingVerificationError(f"observation {observation_id}: postcondition_met must be true")
        if case_type == "success":
            if result_status != "succeeded":
                raise BindingVerificationError(f"observation {observation_id}: success case must succeed")
            if raw["state_before_fingerprint"] == raw["state_after_fingerprint"]:
                raise BindingVerificationError(f"observation {observation_id}: success must change observable state")
        elif result_status not in {"rejected", "failed"}:
            raise BindingVerificationError(f"observation {observation_id}: negative case must reject or fail safely")
        elif raw["state_before_fingerprint"] != raw["state_after_fingerprint"]:
            raise BindingVerificationError(
                f"observation {observation_id}: safe negative case must leave observable state unchanged"
            )
        normalized.append({field: raw[field] for field in sorted(OBSERVATION_FIELDS)} | {"occurred_at": occurred_at})
    return normalized


def verify_bindings(*, project: str | Path, observations: str | Path) -> dict[str, Any]:
    """Validate external traces and promote candidates to non-executing shadow evidence."""
    root = Path(project).expanduser().resolve()
    project_file = root / "forge_project.yaml"
    project_manifest = _load_yaml(project_file)
    if project_manifest.get("stage") != "binding_candidates_ready":
        raise BindingVerificationError(
            f"binding verification requires stage 'binding_candidates_ready', got {project_manifest.get('stage')!r}"
        )
    state_errors = verify_binding_state(root)
    if state_errors:
        raise BindingVerificationError(state_errors[0])
    package_errors = integration_package.verify_package(root)
    if package_errors:
        raise BindingVerificationError(f"integration package is invalid: {package_errors[0]}")
    package = root / "integration"
    binding_manifest_path = package / "binding_manifest.yaml"
    binding_manifest = _load_yaml(binding_manifest_path)
    candidates_path = package / "binding_candidates.yaml"
    expected_hash = ((binding_manifest.get("artifacts") or {}).get("binding_candidates") or {}).get("sha256")
    if _sha256(candidates_path) != expected_hash:
        raise BindingVerificationError("binding candidate hash mismatch")
    candidate_document = _load_yaml(candidates_path)
    candidates = {
        item.get("binding_id"): item
        for item in candidate_document.get("bindings", [])
        if isinstance(item, Mapping) and isinstance(item.get("binding_id"), str)
    }
    if not candidates:
        raise BindingVerificationError("binding candidate registry is empty")

    observation_path = Path(observations).expanduser().resolve()
    normalized = _validate_observations(_load_observations(observation_path), candidates)
    coverage: dict[str, Counter[str]] = defaultdict(Counter)
    harnesses: dict[str, set[tuple[str, str]]] = defaultdict(set)
    harness_coverage: dict[str, dict[tuple[str, str], set[str]]] = defaultdict(lambda: defaultdict(set))
    for item in normalized:
        coverage[item["binding_id"]][item["case_type"]] += 1
        harness = (item["harness_id"], item["harness_sha256"])
        harnesses[item["binding_id"]].add(harness)
        harness_coverage[item["binding_id"]][harness].add(item["case_type"])
    for binding_id in candidates:
        if coverage[binding_id]["success"] < 1:
            raise BindingVerificationError(f"binding {binding_id}: at least one successful observation is required")
        if coverage[binding_id]["negative"] < 1:
            raise BindingVerificationError(f"binding {binding_id}: at least one safe negative observation is required")
        if not any(cases == CASE_TYPES for cases in harness_coverage[binding_id].values()):
            raise BindingVerificationError(
                f"binding {binding_id}: success and negative evidence must come from the same harness digest"
            )

    evidence_dir = package / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    archive_path = evidence_dir / "binding_observations.jsonl"
    if archive_path.exists():
        raise BindingVerificationError("binding observation archive already exists")
    temporary = archive_path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in normalized),
        encoding="utf-8",
    )
    temporary.replace(archive_path)
    observation_hash = _sha256(archive_path)
    verified_at = _now()
    verification = {
        "schema_version": "1.0",
        "system_id": project_manifest["system_id"],
        "stage": "bindings_verified_for_shadow",
        "verified_at": verified_at,
        "observation_source": {
            "file": "integration/evidence/binding_observations.jsonl",
            "sha256": observation_hash,
            "observation_count": len(normalized),
            "raw_state_persisted": False,
            "private_reasoning_persisted": False,
        },
        "bindings": [
            {
                "binding_id": binding_id,
                "binding_digest": candidates[binding_id]["binding_digest"],
                "action_id": candidates[binding_id]["action_id"],
                "status": "verified_for_shadow",
                "evidence_grade": "observed_trace",
                "successful_observations": coverage[binding_id]["success"],
                "negative_observations": coverage[binding_id]["negative"],
                "harnesses": [
                    {"harness_id": harness_id, "sha256": digest}
                    for harness_id, digest in sorted(harnesses[binding_id])
                ],
                "executable": False,
                "production_authority": False,
                "authority": "none",
            }
            for binding_id in sorted(candidates)
        ],
        "verified_binding_count": len(candidates),
        "safety": {
            "forge_executed_target": False,
            "observed_history_is_authority": False,
            "executes_actions": False,
            "production_authority": False,
            "bindings_executable": False,
            "shadow_binding_ready": True,
        },
        "next_action": "implement_and_verify_state_legality_controller_and_bounded_loop",
    }
    verification_path = package / "binding_verification.yaml"
    _write_yaml(verification_path, verification)

    registry_path = package / "action_registry.yaml"
    registry = _load_yaml(registry_path)
    candidate_by_action = {item["action_id"]: item for item in candidates.values()}
    for action in registry.get("actions", []):
        if not isinstance(action, dict) or action.get("action_id") not in candidate_by_action:
            continue
        candidate = candidate_by_action[action["action_id"]]
        action["binding"] = {
            "binding_id": candidate["binding_id"],
            "binding_digest": candidate["binding_digest"],
            "status": "verified_for_shadow",
            "kind": candidate["kind"],
            "locator": candidate["locator"],
            "evidence_grade": "observed_trace",
            "observation_source_sha256": observation_hash,
            "verification_ref": "integration/binding_verification.yaml",
            "executable": False,
            "production_authority": False,
            "authority": "none",
        }
    _write_yaml(registry_path, registry)
    integration_manifest_path = package / "integration_manifest.yaml"
    integration_manifest = _load_yaml(integration_manifest_path)
    integration_manifest["artifacts"]["action_registry"]["sha256"] = _sha256(registry_path)
    _write_yaml(integration_manifest_path, integration_manifest)

    binding_manifest["stage"] = "bindings_verified_for_shadow"
    binding_manifest["updated_at"] = verified_at
    binding_manifest["artifacts"]["binding_observations"] = {
        "file": "integration/evidence/binding_observations.jsonl",
        "sha256": observation_hash,
    }
    binding_manifest["artifacts"]["binding_verification"] = {
        "file": "integration/binding_verification.yaml",
        "sha256": _sha256(verification_path),
    }
    binding_manifest["safety"]["shadow_binding_ready"] = True
    binding_manifest["next_action"] = verification["next_action"]
    _write_yaml(binding_manifest_path, binding_manifest)

    project_manifest["stage"] = "bindings_verified_for_shadow"
    project_manifest["updated_at"] = verified_at
    project_manifest["next_action"] = verification["next_action"]
    project_manifest.setdefault("artifacts", {})["binding_verification"] = "integration/binding_verification.yaml"
    project_manifest.setdefault("summary", {})["verified_shadow_bindings"] = len(candidates)
    project_manifest.setdefault("safety", {}).update(
        {
            "executes_actions": False,
            "production_authority": False,
            "shadow_eligible": False,
            "shadow_binding_ready": True,
        }
    )
    _write_yaml(project_file, project_manifest)
    final_errors = verify_binding_state(root) + integration_package.verify_package(root)
    if final_errors:  # Defensive: generated artifacts should satisfy the same public verifier.
        raise BindingVerificationError(final_errors[0])
    return verification


def verify_binding_state(project: str | Path) -> list[str]:
    """Check binding artifact hashes and cross-file safety invariants."""
    root = Path(project).expanduser().resolve()
    package = root / "integration"
    errors: list[str] = []
    try:
        manifest = _load_yaml(package / "binding_manifest.yaml")
    except BindingVerificationError as exc:
        return [str(exc)]
    stage = manifest.get("stage")
    if stage not in {"binding_candidates_ready", "bindings_verified_for_shadow"}:
        errors.append("binding manifest stage is invalid")
    safety = manifest.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not False
        for field in ("forge_executes_target", "executes_actions", "production_authority", "bindings_executable")
    ):
        errors.append("binding manifest safety boundary is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return errors + ["binding artifact index is missing"]
    required = ["binding_candidates", "binding_observation_schema"]
    if stage == "bindings_verified_for_shadow":
        required.extend(["binding_observations", "binding_verification"])
    for name in required:
        entry = artifacts.get(name)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("file"), str):
            errors.append(f"binding artifact index is invalid for {name}")
            continue
        path = root / entry["file"]
        if not path.is_file():
            errors.append(f"missing binding artifact {name}")
        elif _sha256(path) != entry.get("sha256"):
            label = "binding candidate" if name == "binding_candidates" else f"binding artifact {name}"
            errors.append(f"{label} hash mismatch")
    if errors:
        return errors
    try:
        candidates = _load_yaml(package / "binding_candidates.yaml")
    except BindingVerificationError as exc:
        return [str(exc)]
    items = candidates.get("bindings")
    if not isinstance(items, list) or not items:
        errors.append("binding candidate registry is empty")
    elif any(
        not isinstance(item, Mapping)
        or item.get("executable") is not False
        or item.get("production_authority") is not False
        or item.get("authority") != "none"
        or item.get("binding_digest") != _candidate_digest(item)
        for item in items
    ):
        errors.append("binding candidate registry crosses the execution authority boundary")
    if stage == "bindings_verified_for_shadow":
        verification = _load_yaml(package / "binding_verification.yaml")
        if verification.get("stage") != stage or verification.get("verified_binding_count") != len(items or []):
            errors.append("binding verification coverage is invalid")
        archived = package / "evidence" / "binding_observations.jsonl"
        source = verification.get("observation_source")
        if not isinstance(source, Mapping) or source.get("sha256") != _sha256(archived):
            errors.append("binding observation archive hash mismatch")
        if not isinstance(source, Mapping) or source.get("raw_state_persisted") is not False or source.get("private_reasoning_persisted") is not False:
            errors.append("binding observation privacy boundary is invalid")
    try:
        registry = _load_yaml(package / "action_registry.yaml")
    except BindingVerificationError as exc:
        return errors + [str(exc)]
    actions = {
        item.get("action_id"): item
        for item in registry.get("actions", [])
        if isinstance(item, Mapping) and isinstance(item.get("action_id"), str)
    }
    for candidate in items or []:
        if not isinstance(candidate, Mapping):
            continue
        action = actions.get(candidate.get("action_id"))
        if action is None:
            errors.append(f"binding {candidate.get('binding_id')}: action is absent from the action registry")
            continue
        binding = action.get("binding")
        if stage == "binding_candidates_ready":
            if not isinstance(binding, Mapping) or binding.get("status") != "stub":
                errors.append(f"binding {candidate.get('binding_id')}: candidate action is no longer a stub")
            continue
        expected = {
            "binding_id": candidate.get("binding_id"),
            "binding_digest": candidate.get("binding_digest"),
            "status": "verified_for_shadow",
            "kind": candidate.get("kind"),
            "locator": candidate.get("locator"),
            "evidence_grade": "observed_trace",
            "observation_source_sha256": source.get("sha256") if isinstance(source, Mapping) else None,
            "verification_ref": "integration/binding_verification.yaml",
            "executable": False,
            "production_authority": False,
            "authority": "none",
        }
        if binding != expected:
            errors.append(f"binding {candidate.get('binding_id')}: promoted action binding does not match verified candidate")
    return errors


__all__ = [
    "BindingVerificationError",
    "propose_bindings",
    "verify_bindings",
    "verify_binding_state",
    "resolve_evidence_sources",
]
