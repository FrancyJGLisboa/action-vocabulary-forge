#!/usr/bin/env python3
"""Evaluate a decision log against ground truth and print the release gate.

Metrics follow references/evaluation.md. Log-derived metrics use the held-out
split; bundle-static metrics (action precision, surface coverage, replacement
rate) come from the bundle files. Exit 0 on APPROVE, 2 on HOLD.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_history import (  # noqa: E402
    EMPTY_METRICS, attach_labels, fmt, gate_failures, human_labeled, is_deterministic, load_questions, log_metrics as _log_metrics,
    question_executors, question_fallbacks, ratio, read_labels, read_log, split_cases, threshold_failures,
)
from validate_action_bundle import PRODUCTION_GRADES, validate  # noqa: E402


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def static_metrics(bundle: Path) -> dict[str, Any]:
    actions = {a["action_id"]: a for a in load_yaml(bundle / "action_registry.yaml").get("actions", []) if isinstance(a, dict)}
    grades = {}
    ledger = bundle / "evidence_ledger.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                grades[item.get("evidence_id")] = item.get("grade")
    supported = sum(1 for a in actions.values() if any(grades.get(r) in PRODUCTION_GRADES for r in a.get("evidence_refs", [])))
    candidates = [c for c in load_yaml(bundle / "surface_candidates.yaml").get("surface_candidates", []) if isinstance(c, dict)]
    surfaces = [s for s in load_yaml(bundle / "decision_surfaces.yaml").get("decision_surfaces", []) if isinstance(s, dict)]
    _, questions = load_questions(bundle)
    production_surfaces = {s["surface_id"] for s in surfaces if s.get("production") and any(q.get("surface_id") == s["surface_id"] for q in questions.values())}
    bounded = [c for c in candidates if c.get("bounded_output")]
    promoted_bounded = [c for c in bounded if c.get("review_status") == "promoted"]
    covered = len(promoted_bounded) if production_surfaces else 0
    generative = [c for c in candidates if c.get("current_implementation") == "generative_call"]
    replaced = [c for c in generative if c.get("review_status") == "promoted"]
    return {
        "action_precision": ratio(supported, len(actions)),
        "surface_coverage": ratio(min(covered, len(bounded)), len(bounded)),
        "replacement_rate": ratio(len(replaced), len(generative)),
        "registry_actions": set(actions),
    }


def log_metrics(records: list[dict[str, Any]], questions: dict[str, dict[str, Any]], registry_actions: set[str]) -> dict[str, Any]:
    return _log_metrics(records, {qid: question_fallbacks(q) for qid, q in questions.items()}, registry_actions)


def release_gate(
    bundle: Path,
    metrics: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    min_boundary: float,
    min_abstention: float,
    min_samples: int,
) -> tuple[bool, list[str]]:
    spec, questions = load_questions(bundle)
    fallbacks = {qid: question_fallbacks(q) for qid, q in questions.items()}
    failures = gate_failures(metrics, records, min_boundary=min_boundary, min_abstention=min_abstention, min_samples=min_samples)
    for qid in questions:
        if not fallbacks[qid]:
            failures.append(f"question {qid} has no abstention path")
    answers = {qid: question_executors(q) for qid, q in questions.items()}
    failures += threshold_failures(records, fallbacks, spec.get("policy") or {}, answers)
    errors, warnings, _ = validate(bundle)
    for error in errors:
        failures.append(f"bundle invalid: {error}")
    return not failures, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--heldout-fraction", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--all", action="store_true", help="evaluate every labeled record instead of the held-out split")
    parser.add_argument("--min-boundary-accuracy", type=float, default=0.9)
    parser.add_argument("--min-abstention-accuracy", type=float, default=0.9)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--by-model", action="store_true", help="also print the log metrics per model value (e.g. JEV vs a local model)")
    args = parser.parse_args(argv)

    spec, questions = load_questions(args.bundle)
    records = read_log(args.log)
    labeled, dropped = attach_labels(records, read_labels(args.labels, with_source=True))
    if args.all:
        evaluated = labeled
    else:
        _, evaluated = split_cases(labeled, args.heldout_fraction, args.seed)
    # The gate never grades against machine labels: an LLM that labels and escalates would grade itself.
    evaluated, machine_labeled = human_labeled(evaluated)
    static = static_metrics(args.bundle)
    metrics = log_metrics(evaluated, questions, static["registry_actions"]) if evaluated else dict(EMPTY_METRICS)
    metrics.update({k: static[k] for k in ("action_precision", "surface_coverage", "replacement_rate")})
    approved, failures = release_gate(
        args.bundle, metrics, evaluated,
        min_boundary=args.min_boundary_accuracy, min_abstention=args.min_abstention_accuracy, min_samples=args.min_samples,
    )

    deterministic = [r for r in evaluated if is_deterministic(r)]
    det_agree = sum(1 for r in deterministic if r.get("action_id") == r.get("ground_truth_action_id"))
    metrics["deterministic"] = ratio(det_agree, len(deterministic))
    print(f"system: {spec.get('system_id')}  records={len(records)} labeled={len(labeled)} dropped_unlabeled={dropped} excluded_machine_labeled={machine_labeled} evaluated={len(evaluated)}")
    for key in ("action_recall", "action_precision", "surface_coverage", "boundary_accuracy", "abstention_accuracy", "illegal_action_rate", "jev_accuracy", "escalation_accuracy", "replacement_rate"):
        print(f"  {key:22} {fmt(metrics[key])}")
    lat = metrics["latency_ms"]
    mean_ms = "n/a" if lat["mean"] is None else format(lat["mean"], ".1f")
    p95_ms = "n/a" if lat["p95"] is None else format(lat["p95"], ".1f")
    print("  {:22} mean={} p95={}".format("latency_ms", mean_ms, p95_ms))
    print(f"  {'usage':22} {metrics['usage'] or 'n/a'}")
    print(f"  {'deterministic':22} {fmt(metrics['deterministic'])} decided by code, agreement with labels (must be 100%)")
    if args.by_model:
        by_model: dict[str, list[dict[str, Any]]] = {}
        for r in evaluated:
            if not is_deterministic(r):
                by_model.setdefault(str(r.get("model") or "unknown"), []).append(r)
        if by_model:
            print("\nby model:")
            print(f"  {'model':28} {'n':>4}  {'jev_accuracy':>14}  {'boundary':>12}  {'abstention':>12}  {'illegal':>8}  {'p95_ms':>7}")
            for model_name, subset in sorted(by_model.items()):
                m = log_metrics(subset, questions, static["registry_actions"])
                p95 = m["latency_ms"]["p95"]
                p95_text = "n/a" if p95 is None else format(p95, ".0f")
                print("  {:28} {:4d}  {:>14}  {:>12}  {:>12}  {:8d}  {:>7}".format(
                    model_name, len(subset), fmt(m["jev_accuracy"]), fmt(m["boundary_accuracy"]),
                    fmt(m["abstention_accuracy"]), m["illegal_action_rate"]["count"], p95_text))
            metrics["by_model"] = {name: {k: log_metrics(subset, questions, static["registry_actions"])[k] for k in ("jev_accuracy", "boundary_accuracy", "abstention_accuracy", "illegal_action_rate", "latency_ms")} for name, subset in by_model.items()}
    print()
    print("RELEASE GATE: " + ("APPROVE" if approved else "HOLD"))
    for failure in failures:
        print(f"  - {failure}")
    surfaces = sorted({q.get("surface_id") for q in questions.values()})
    if approved:
        print(f"\nApprove release of surfaces {surfaces} for system {spec.get('system_id')}? [yes/no]")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"approved": approved, "failures": failures, "metrics": {k: v for k, v in metrics.items() if k != "calibration"}, "calibration": metrics.get("calibration"), "evaluated": len(evaluated)}, indent=2, default=str), encoding="utf-8")
    return 0 if approved else 2


if __name__ == "__main__":
    raise SystemExit(main())
