#!/usr/bin/env python3
"""Connect a reviewed Forge shadow controller to TypeSafe JEV.

The module is intentionally non-executing. It reads observations, lets the
controller compute the legal action set, asks one bounded Choice question, and
persists fingerprints plus typed answer metadata. It never imports or invokes a
binding and never persists the API key or raw observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

import shadow_controller


DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
Transport = Callable[[dict[str, Any]], Mapping[str, Any]]


class JevShadowError(RuntimeError):
    """The JEV shadow boundary could not be used safely."""


class JevAuthenticationError(JevShadowError):
    """TypeSafe credentials are absent or rejected."""


class JevTransportError(JevShadowError):
    """The TypeSafe request could not be completed."""


class JevResponseError(JevShadowError):
    """TypeSafe returned a response outside the bounded contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("jev-%Y%m%dT%H%M%S%fZ")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def typesafe_transport(
    endpoint: str | None = None,
    timeout: float = 30.0,
) -> Transport:
    """Return a standard-library TypeSafe HTTP transport.

    Credentials are captured from ``TYPESAFE_API_KEY`` when the transport is
    created. They are used only in the Authorization header and are excluded
    from errors, return values, representations, and persisted receipts.
    """
    token = os.environ.get("TYPESAFE_API_KEY")
    if not token:
        raise JevAuthenticationError("TYPESAFE_API_KEY is not set")
    url = endpoint or os.environ.get("TYPESAFE_API_URL", DEFAULT_ENDPOINT)
    if not isinstance(url, str) or not url.startswith("https://"):
        raise JevTransportError("TypeSafe endpoint must use HTTPS")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise JevTransportError("TypeSafe timeout must be greater than zero")

    def send(payload: dict[str, Any]) -> Mapping[str, Any]:
        try:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise JevTransportError("TypeSafe payload must be JSON serializable") from exc
        request = urllib.request.Request(
            url,
            data=encoded,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=float(timeout)) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status in {401, 403}:
                raise JevAuthenticationError(f"TypeSafe authentication failed (HTTP {status})") from exc
            if status == 429:
                raise JevTransportError("TypeSafe rate limit reached (HTTP 429)") from exc
            raise JevTransportError(f"TypeSafe request failed (HTTP {status})") from exc
        except urllib.error.URLError as exc:
            raise JevTransportError("TypeSafe connection failed") from exc
        except TimeoutError as exc:
            raise JevTransportError("TypeSafe request timed out") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JevResponseError("TypeSafe response was not valid JSON") from exc
        if not isinstance(result, Mapping):
            raise JevResponseError("TypeSafe response must be a mapping")
        return result

    return send


def _number(value: Any, *, owner: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevResponseError(f"{owner} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise JevResponseError(f"{owner} must be between 0 and 1")
    return number


def _safe_usage(value: Any) -> dict[str, int | float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise JevResponseError("TypeSafe usage must be a mapping")
    usage: dict[str, int | float] = {}
    for key, amount in value.items():
        if not isinstance(key, str) or len(key) > 64:
            raise JevResponseError("TypeSafe usage keys must be short strings")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(float(amount))
            or amount < 0
        ):
            raise JevResponseError("TypeSafe usage values must be non-negative numbers")
        usage[key] = amount
    return usage


class TypeSafeShadowDecider:
    """Adapt one reviewed controller plan to bounded TypeSafe Choice calls."""

    def __init__(
        self,
        plan: Mapping[str, Any],
        *,
        transport: Transport,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.plan = dict(plan)
        self.transport = transport
        self.model = model
        surface = self.plan.get("surface") or {}
        self.question_id = str(surface.get("question_id", ""))
        self.fallback_action_id = str(surface.get("fallback_action_id", ""))
        self.action_rules = {
            str(item["action_id"]): dict(item)
            for item in surface.get("actions", [])
            if isinstance(item, Mapping) and isinstance(item.get("action_id"), str)
        }

    def _criterion(self, action_id: str) -> dict[str, Any]:
        rule = self.action_rules.get(action_id)
        if rule is None:
            raise JevResponseError(f"controller offered unknown action {action_id!r}")
        criterion: dict[str, Any] = {
            "action": rule.get("label") or action_id,
        }
        preconditions = rule.get("preconditions") or []
        if preconditions:
            criterion["when"] = list(preconditions)
        if action_id == self.fallback_action_id:
            criterion["fallback"] = "Use when no non-fallback action is sufficiently supported or human review is needed."
        return criterion

    def __call__(
        self,
        question: str,
        state: Mapping[str, Any],
        legal_action_ids: list[str],
    ) -> Mapping[str, Any]:
        if not legal_action_ids or len(set(legal_action_ids)) != len(legal_action_ids):
            raise JevResponseError("controller must offer a non-empty unique legal action set")
        if len(legal_action_ids) == 1:
            return {
                "answer": legal_action_ids[0],
                "confidence": 1.0,
                "probabilities": {legal_action_ids[0]: 1.0},
                "model": "deterministic-single-legal-action",
                "usage": {},
                "latency_ms": 0.0,
                "provider": "deterministic",
            }
        criteria = {action_id: self._criterion(action_id) for action_id in legal_action_ids}
        payload = {
            "model": self.model,
            "state": {
                "context": dict(state),
                "system_id": self.plan.get("system_id"),
                "decision_surface": (self.plan.get("surface") or {}).get("surface_id"),
                "observable_only": True,
                "legal_action_ids": list(legal_action_ids),
            },
            "questions": {
                self.question_id: {
                    "type": "choice",
                    "instructions": question,
                    "criteria": criteria,
                }
            },
        }
        started = time.perf_counter()
        response = self.transport(payload)
        latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
        answers = response.get("answers")
        if not isinstance(answers, Mapping):
            raise JevResponseError("TypeSafe response omitted answers")
        raw_answer = answers.get(self.question_id)
        if not isinstance(raw_answer, Mapping):
            raise JevResponseError(f"TypeSafe response omitted answer {self.question_id!r}")
        selected = raw_answer.get("choice")
        if selected not in legal_action_ids:
            raise JevResponseError(f"TypeSafe returned an action outside the legal set: {selected!r}")
        confidence = _number(raw_answer.get("confidence"), owner="TypeSafe confidence")
        raw_probabilities = raw_answer.get("probabilities")
        if not isinstance(raw_probabilities, Mapping) or set(raw_probabilities) != set(legal_action_ids):
            raise JevResponseError("TypeSafe probabilities must cover exactly the legal action set")
        probabilities = {
            action_id: _number(raw_probabilities[action_id], owner=f"probability {action_id}")
            for action_id in legal_action_ids
        }
        if abs(sum(probabilities.values()) - 1.0) > 0.02:
            raise JevResponseError("TypeSafe probabilities must sum to one")
        response_model = response.get("model", self.model)
        if not isinstance(response_model, str) or not response_model or len(response_model) > 128:
            raise JevResponseError("TypeSafe model must be a short string")
        return {
            "answer": selected,
            "confidence": confidence,
            "probabilities": probabilities,
            "model": response_model,
            "usage": _safe_usage(response.get("usage")),
            "latency_ms": latency_ms,
            "provider": "typesafe",
        }


def load_observations(path: str | Path) -> list[Mapping[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise JevShadowError(f"observation file does not exist: {source}")
    observations: list[Mapping[str, Any]] = []
    try:
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise JevShadowError(f"observation line {line_number} must contain a JSON object")
            observations.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JevShadowError(f"cannot read observation JSONL: {exc}") from exc
    if not observations:
        raise JevShadowError("observation JSONL contains no observations")
    return observations


def run_controller_shadow(
    project: str | Path,
    *,
    observations: str | Path,
    transport: Transport | None = None,
    model: str = DEFAULT_MODEL,
    endpoint: str | None = None,
    timeout: float = 30.0,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one finite JEV-backed controller pass and write privacy-safe receipts."""
    root = Path(project).expanduser().resolve()
    source = Path(observations).expanduser().resolve()
    runtime = shadow_controller.load_runtime(root)
    selected_run_id = run_id or _default_run_id()
    if not RUN_ID.fullmatch(selected_run_id):
        raise JevShadowError("run_id must be a stable filename-safe identifier")
    output_dir = root / "integration" / "shadow_runs"
    records_path = output_dir / f"{selected_run_id}.jsonl"
    manifest_path = output_dir / f"{selected_run_id}.yaml"
    if records_path.exists() or manifest_path.exists():
        raise JevShadowError(f"shadow run {selected_run_id!r} already exists")
    send = transport or typesafe_transport(endpoint=endpoint, timeout=timeout)
    decider = TypeSafeShadowDecider(runtime.plan, transport=send, model=model)
    loaded_observations = load_observations(source)
    result = runtime.run(loaded_observations, decider)
    records = result.get("records") or []
    records_text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    _atomic_text(records_path, records_text)
    relative_records = records_path.relative_to(root).as_posix()
    relative_manifest = manifest_path.relative_to(root).as_posix()
    manifest = {
        "schema_version": "1.0",
        "run_id": selected_run_id,
        "created_at": _now(),
        "system_id": runtime.plan.get("system_id"),
        "provider": "typesafe" if transport is None else "injected_transport",
        "requested_model": model,
        "question_id": (runtime.plan.get("surface") or {}).get("question_id"),
        "controller_digest": runtime.plan.get("plan_digest"),
        "source": {
            "observations_sha256": sha256_file(source),
            "observation_count": len(loaded_observations),
            "raw_observations_persisted": False,
        },
        "result": {
            "stop_reason": result.get("stop_reason"),
            "iterations": result.get("iterations"),
            "record_count": len(records),
        },
        "artifacts": {"records": {"file": relative_records, "sha256": sha256_file(records_path)}},
        "safety": {
            "shadow_only": True,
            "executes_actions": False,
            "binding_invocations": result.get("binding_invocations", 0),
            "production_authority": False,
        },
        "next_action": "collect_trusted_labels_and_calibrate_exact_question_state_builder_and_model",
    }
    _atomic_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
    return {
        "status": "shadow_run_recorded",
        "run_id": selected_run_id,
        "stop_reason": result.get("stop_reason"),
        "iterations": result.get("iterations"),
        "binding_invocations": result.get("binding_invocations", 0),
        "production_authority": False,
        "artifacts": {"records": relative_records, "manifest": relative_manifest},
    }


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "JevAuthenticationError",
    "JevResponseError",
    "JevShadowError",
    "JevTransportError",
    "TypeSafeShadowDecider",
    "load_observations",
    "run_controller_shadow",
    "sha256_file",
    "typesafe_transport",
]
