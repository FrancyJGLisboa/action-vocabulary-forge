#!/usr/bin/env python3
"""Run a discovered candidate Choice in shadow mode on its held-out cases.

This script never executes an action. It reloads the original case source from
the discovery manifest, reconstructs the deterministic holdout, sends only the
candidate Choice to TypeSafe JEV or Laya, validates the returned option, and
writes privacy-safe performance records compatible with ``jev_gate.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover_decision_system as discovery  # noqa: E402
from transports import laya_transport  # noqa: E402


Transport = Callable[[dict[str, Any]], dict[str, Any]]
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class EvaluationError(RuntimeError):
    """Discovery output or model response cannot support a safe evaluation."""


def _yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"{path} must contain a mapping")
    return value


def typesafe_transport(endpoint: str | None = None, api_key: str | None = None, timeout: float = 30.0) -> Transport:
    token = api_key or os.environ.get("TYPESAFE_API_KEY")
    if not token:
        raise EvaluationError("TYPESAFE_API_KEY is not set")
    url = endpoint or os.environ.get("TYPESAFE_API_URL", DEFAULT_ENDPOINT)
    if not url.startswith("https://"):
        raise EvaluationError("TypeSafe endpoint must use https://")

    def send(payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"JEV request failed: {exc}") from exc
        if not isinstance(result, dict):
            raise EvaluationError("JEV response must be a mapping")
        return result

    return send


def _confidence(answer: Mapping[str, Any]) -> float | None:
    value = answer.get("confidence")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    probabilities = answer.get("probabilities")
    if isinstance(probabilities, Mapping) and probabilities:
        try:
            return max(float(item) for item in probabilities.values())
        except (TypeError, ValueError):
            return None
    return None


def evaluate(
    discovery_dir: str | Path,
    *,
    transport: Transport,
    output: str | Path | None = None,
    label_source: str = "historical",
    model: str | None = None,
) -> dict[str, Any]:
    root = Path(discovery_dir)
    manifest = _yaml(root / "discovery_manifest.yaml")
    adapter = _yaml(root / "candidate_bundle" / "jev_adapter_spec.yaml")
    surfaces = _yaml(root / "candidate_bundle" / "decision_surfaces.yaml")
    case_config = manifest.get("cases") or {}
    fields = case_config.get("fields") or {}
    cases_path = Path(str(case_config.get("locator", "")))
    expected_hash = case_config.get("sha256")
    if not cases_path.is_file():
        raise EvaluationError(f"original cases file is unavailable: {cases_path}")
    if expected_hash and discovery._sha256(cases_path) != expected_hash:
        raise EvaluationError("original cases file changed after discovery; rerun discovery")
    required_fields = {"case_id", "text", "action"}
    if not required_fields.issubset(fields):
        raise EvaluationError("discovery manifest lacks the case field contract; rerun discovery")
    cases = discovery.load_cases(
        cases_path,
        case_id_field=fields["case_id"],
        text_field=fields["text"],
        action_field=fields["action"],
        outcome_field=fields.get("outcome"),
        surface_field=fields.get("surface"),
    )
    surface_values = surfaces.get("decision_surfaces") or []
    if len(surface_values) != 1:
        raise EvaluationError("candidate bundle must contain exactly one decision surface")
    surface_id = surface_values[0].get("surface_id")
    surface_cases = [case for case in cases if case.surface == surface_id]
    _, holdout = discovery.stratified_split(surface_cases, float(case_config.get("test_fraction", 0.2)))
    if not holdout:
        raise EvaluationError("candidate surface has no holdout cases")
    questions = adapter.get("classifier_questions") or []
    if len(questions) != 1 or questions[0].get("type", "choice") != "choice":
        raise EvaluationError("candidate bundle must contain exactly one Choice question")
    question = questions[0]
    question_id = question.get("question_id")
    if not isinstance(question_id, str) or not question_id:
        raise EvaluationError("candidate Choice is missing question_id")
    choices = question.get("choices") or []
    criteria = {
        item.get("id"): item.get("criterion") or item.get("id")
        for item in choices
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if len(criteria) < 2:
        raise EvaluationError("candidate Choice has fewer than two options")
    allowed = set(criteria)
    records = []
    for case in sorted(holdout, key=lambda item: item.case_id):
        payload = {
            "model": model or adapter.get("model", "jev-latest"),
            "state": {
                "context": case.text,
                "system_id": adapter.get("system_id"),
                "decision_surface": surface_id,
                "shadow_mode": True,
                "allowed_actions": sorted(allowed),
            },
            "questions": {
                question_id: {
                    "type": "choice",
                    "instructions": question.get("instruction") or "Choose the best-supported candidate action.",
                    "criteria": criteria,
                }
            },
        }
        started = time.perf_counter()
        response = transport(payload)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if not isinstance(response, Mapping):
            raise EvaluationError("transport returned no response mapping")
        answer = (response.get("answers") or {}).get(question_id)
        if not isinstance(answer, Mapping):
            raise EvaluationError(f"response omitted answer {question_id!r}")
        selected = answer.get("choice")
        if selected not in allowed:
            raise EvaluationError(f"model returned illegal choice {selected!r}; allowed={sorted(allowed)}")
        case_ref = hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:16]
        records.append(
            {
                "case_id": case_ref,
                "question_id": question_id,
                "jev": dict(answer),
                "truth": case.action_id,
                "legal": sorted(allowed),
                "label_source": label_source,
                "model": response.get("model", payload["model"]),
                "usage": response.get("usage"),
                "latency_ms": round(latency_ms, 3),
            }
        )
    correct = sum(item["jev"].get("choice") == item["truth"] for item in records)
    confidences = [_confidence(item["jev"]) for item in records]
    threshold = float(case_config.get("confidence_threshold", 0.8))
    confident = [
        item
        for item, confidence in zip(records, confidences)
        if confidence is not None and confidence >= threshold
    ]
    metrics = {
        "status": "measured_jev_holdout",
        "surface_id": surface_id,
        "question_id": question_id,
        "holdout_cases": len(records),
        "raw_accuracy": round(correct / len(records), 4),
        "confidence_threshold": threshold,
        "confident_coverage": round(len(confident) / len(records), 4),
        "confident_accuracy": round(
            sum(item["jev"].get("choice") == item["truth"] for item in confident) / len(confident), 4
        ) if confident else None,
        "illegal_answers": 0,
        "label_source": label_source,
        "release_gate_eligible": label_source == "human",
        "release_status": "not_run",
        "note": "Run jev_gate.py with trusted human labels before any release decision.",
    }
    destination = Path(output) if output is not None else root / "jev_holdout_log.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
    metrics_path = destination.with_name("jev_performance.json")
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return {**metrics, "log": str(destination), "metrics": str(metrics_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("discovery", type=Path)
    parser.add_argument("--provider", choices=["typesafe", "laya"], default="typesafe")
    parser.add_argument("--model")
    parser.add_argument("--endpoint")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label-source", default="historical")
    args = parser.parse_args(argv)
    try:
        if args.provider == "laya":
            send = laya_transport(args.model or "laya:typed-decisions")
        else:
            send = typesafe_transport(args.endpoint, None, args.timeout)
        result = evaluate(
            args.discovery,
            transport=send,
            output=args.output,
            label_source=args.label_source,
            model=args.model,
        )
    except (EvaluationError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
