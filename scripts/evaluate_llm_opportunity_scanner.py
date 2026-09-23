#!/usr/bin/env python3
"""Evaluate pre-JEV call-site discovery against a frozen labeled fixture set."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "llm-opportunity-eval" / "cases.yaml"
LABELS = {
    "replaceable_decision",
    "open_ended_generation",
    "provider_infrastructure",
    "not_ai",
}
ELIGIBLE_LABELS = {"replaceable_decision", "open_ended_generation"}


def _load_scanner():
    path = Path(__file__).with_name("scan_llm_opportunities.py")
    spec = importlib.util.spec_from_file_location("llm_opportunity_scanner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scanner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCANNER = _load_scanner()


class EvaluationError(ValueError):
    pass


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationError(f"cannot load cases: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != "1.0":
        raise EvaluationError("case file must be a schema_version 1.0 mapping")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationError("case file must contain a non-empty cases list")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise EvaluationError(f"case {index} must be a mapping")
        case_id = case.get("case_id")
        filename = case.get("filename")
        label = case.get("label")
        source = case.get("source")
        source_reference = case.get("source_reference")
        expected_lines = case.get("expected_lines")
        if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9_]+", case_id):
            raise EvaluationError(f"case {index} has an invalid case_id")
        if case_id in seen:
            raise EvaluationError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise EvaluationError(f"case {case_id} filename must be a basename")
        if label not in LABELS:
            raise EvaluationError(f"case {case_id} has unknown label: {label!r}")
        if not isinstance(source, str):
            raise EvaluationError(f"case {case_id} source must be text")
        if source_reference is not None and (
            not isinstance(source_reference, str)
            or not source_reference.startswith("https://github.com/")
        ):
            raise EvaluationError(f"case {case_id} source_reference must be a GitHub HTTPS URL")
        if (
            not isinstance(expected_lines, list)
            or any(isinstance(line, bool) or not isinstance(line, int) or line < 1 for line in expected_lines)
            or len(set(expected_lines)) != len(expected_lines)
        ):
            raise EvaluationError(f"case {case_id} expected_lines must contain unique positive integers")
        if (label in ELIGIBLE_LABELS) != bool(expected_lines):
            raise EvaluationError(f"case {case_id} label and expected_lines disagree")
        validated.append(case)
    return validated


def evaluate_cases(
    cases: list[dict[str, Any]],
    *,
    min_precision: float = 0.9,
    min_recall: float = 0.8,
) -> dict[str, Any]:
    for name, value in (("min_precision", min_precision), ("min_recall", min_recall)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise EvaluationError(f"{name} must be between 0 and 1")

    true_positive = false_positive = false_negative = 0
    true_negative_cases = 0
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="forge-scanner-eval-") as tmp:
        root = Path(tmp)
        for case in cases:
            case_root = root / case["case_id"]
            case_root.mkdir()
            source_path = case_root / case["filename"]
            source_path.write_text(case["source"], encoding="utf-8")
            candidates = SCANNER.scan_file(source_path, case_root)
            predicted = {
                int(candidate["source_locator"].rsplit(":", 1)[1])
                for candidate in candidates
            }
            expected = set(case["expected_lines"])
            matched = predicted & expected
            unexpected = predicted - expected
            missed = expected - predicted
            true_positive += len(matched)
            false_positive += len(unexpected)
            false_negative += len(missed)
            if not expected and not predicted:
                true_negative_cases += 1
            results.append(
                {
                    "case_id": case["case_id"],
                    "label": case["label"],
                    "source_reference": case.get("source_reference"),
                    "expected_lines": sorted(expected),
                    "predicted_lines": sorted(predicted),
                    "false_positive_lines": sorted(unexpected),
                    "missed_lines": sorted(missed),
                }
            )

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    approved = precision >= min_precision and recall >= min_recall
    return {
        "case_count": len(cases),
        "label_counts": {label: sum(case["label"] == label for case in cases) for label in sorted(LABELS)},
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative_cases": true_negative_cases,
        "precision": precision,
        "recall": recall,
        "minimum_precision": float(min_precision),
        "minimum_recall": float(min_recall),
        "approved": approved,
        "cases": results,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"cases: {report['case_count']}")
    print(
        "call sites: "
        f"true_positive={report['true_positive']} "
        f"false_positive={report['false_positive']} "
        f"false_negative={report['false_negative']}"
    )
    print(f"precision: {report['precision']:.3f} (minimum {report['minimum_precision']:.3f})")
    print(f"recall: {report['recall']:.3f} (minimum {report['minimum_recall']:.3f})")
    failures = [case for case in report["cases"] if case["false_positive_lines"] or case["missed_lines"]]
    for case in failures:
        print(
            f"FAIL {case['case_id']} ({case['label']}): "
            f"unexpected={case['false_positive_lines']} missed={case['missed_lines']}"
        )
    print(f"SCANNER RELEASE GATE: {'APPROVE' if report['approved'] else 'HOLD'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--min-precision", type=float, default=0.9)
    parser.add_argument("--min-recall", type=float, default=0.8)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        report = evaluate_cases(
            cases,
            min_precision=args.min_precision,
            min_recall=args.min_recall,
        )
    except EvaluationError as exc:
        raise SystemExit(f"evaluation error: {exc}") from exc
    print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["approved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
