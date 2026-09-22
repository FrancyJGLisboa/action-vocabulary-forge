#!/usr/bin/env python3
"""Evaluate a decision log against ground truth and print the release gate.

Metrics follow references/evaluation.md. Log-derived metrics use the held-out
split; bundle-static metrics (action precision, surface coverage, replacement
rate) come from the bundle files. Exit 0 on APPROVE, 2 on HOLD.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_history import (  # noqa: E402
    attach_labels, calibration_bins, is_deterministic, load_questions, question_executors, question_fallbacks, read_labels, read_log,
    split_cases,
)
from validate_action_bundle import PRODUCTION_GRADES, validate  # noqa: E402


def ratio(count: int, total: int) -> dict[str, Any]:
    return {"count": count, "total": total, "pct": (count / total) if total else None}


def fmt(value: dict[str, Any]) -> str:
    pct = "n/a" if value["pct"] is None else f"{value['pct'] * 100:.1f}%"
    return f"{value['count']}/{value['total']} ({pct})"


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
    fallbacks = {qid: question_fallbacks(q) for qid, q in questions.items()}
    truths = {r["ground_truth_action_id"] for r in records}
    recall = ratio(len(truths & registry_actions), len(truths))
    decided = [r for r in records if not r.get("abstained")]
    boundary = ratio(sum(1 for r in decided if r.get("action_id") == r["ground_truth_action_id"]), len(decided))
    need_abstain = [r for r in records if r["ground_truth_action_id"] in fallbacks.get(r.get("question_id"), set())]
    abstained_ok = sum(1 for r in need_abstain if r.get("abstained") or r.get("action_id") in fallbacks.get(r.get("question_id"), set()))
    abstention = ratio(abstained_ok, len(need_abstain))
    illegal = [
        r for r in records
        if (isinstance(r.get("legal_actions"), list) and r.get("proposed_action_id") not in r["legal_actions"] and r.get("proposed_action_id") not in fallbacks.get(r.get("question_id"), set()))
        or (r.get("outcome") == "blocked" and any(marker in str(r.get("blocked_reason")) for marker in ("illegal from state", "preconditions failed")))
    ]
    jev = ratio(sum(1 for r in records if r.get("proposed_action_id") == r["ground_truth_action_id"]), len(records))
    latencies = [float(r["latency_ms"]) for r in records if r.get("latency_ms") is not None]
    usage_in = sum(int((r.get("usage") or {}).get("input_tokens", 0) or 0) for r in records)
    usage_out = sum(int((r.get("usage") or {}).get("output_tokens", 0) or 0) for r in records)
    return {
        "action_recall": recall,
        "boundary_accuracy": boundary,
        "abstention_accuracy": abstention,
        "illegal_action_rate": ratio(len(illegal), len(records)),
        "illegal_records": [r.get("case_id") for r in illegal],
        "jev_accuracy": jev,
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p95": (sorted(latencies)[max(0, int(round(0.95 * len(latencies))) - 1)] if latencies else None),
        },
        "usage": {"input_tokens": usage_in, "output_tokens": usage_out} if (usage_in or usage_out) else None,
        "calibration": {qid: calibration_bins([r for r in records if r.get("question_id") == qid]) for qid in questions},
    }


def release_gate(
    bundle: Path,
    metrics: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    min_boundary: float,
    min_abstention: float,
    min_samples: int,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    spec, questions = load_questions(bundle)
    if metrics["illegal_action_rate"]["count"]:
        failures.append(f"illegal actions on held-out: {metrics['illegal_action_rate']['count']} (cases {metrics['illegal_records'][:5]})")
    for qid, question in questions.items():
        if not question_fallbacks(question):
            failures.append(f"question {qid} has no abstention path")
    policy = spec.get("policy") or {}
    scoped = policy.get("questions") or {}
    for qid, question in questions.items():
        seen = {r.get("proposed_action_id") for r in records if r.get("question_id") == qid}
        for action_id in question_executors(question) - question_fallbacks(question):
            if action_id not in seen:
                continue
            per_action = ((scoped.get(qid) or {}).get("actions") or {}).get(action_id)
            per_question = (scoped.get(qid) or {}).get("min_confidence")
            if per_action is None and per_question is None and policy.get("min_confidence") is None and policy.get("default_when_uncalibrated") != "allow":
                failures.append(f"question {qid} action {action_id}: no calibrated threshold (uncalibrated => abstain)")
    if metrics["boundary_accuracy"]["pct"] is not None and metrics["boundary_accuracy"]["pct"] < min_boundary:
        failures.append(f"boundary accuracy {fmt(metrics['boundary_accuracy'])} below {min_boundary:.0%}")
    if metrics["abstention_accuracy"]["pct"] is not None and metrics["abstention_accuracy"]["pct"] < min_abstention:
        failures.append(f"abstention accuracy {fmt(metrics['abstention_accuracy'])} below {min_abstention:.0%}")
    if len(records) < min_samples:
        failures.append(f"held-out has {len(records)} records, fewer than {min_samples}")
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
    labeled, dropped = attach_labels(records, read_labels(args.labels))
    if args.all:
        evaluated = labeled
    else:
        _, evaluated = split_cases(labeled, args.heldout_fraction, args.seed)
    static = static_metrics(args.bundle)
    metrics = log_metrics(evaluated, questions, static["registry_actions"]) if evaluated else {
        "action_recall": ratio(0, 0), "boundary_accuracy": ratio(0, 0), "abstention_accuracy": ratio(0, 0),
        "illegal_action_rate": ratio(0, 0), "illegal_records": [], "jev_accuracy": ratio(0, 0),
        "latency_ms": {"mean": None, "p95": None}, "usage": None, "calibration": {},
    }
    metrics.update({k: static[k] for k in ("action_precision", "surface_coverage", "replacement_rate")})
    approved, failures = release_gate(
        args.bundle, metrics, evaluated,
        min_boundary=args.min_boundary_accuracy, min_abstention=args.min_abstention_accuracy, min_samples=args.min_samples,
    )

    deterministic = [r for r in evaluated if is_deterministic(r)]
    det_agree = sum(1 for r in deterministic if r.get("action_id") == r.get("ground_truth_action_id"))
    metrics["deterministic"] = ratio(det_agree, len(deterministic))
    print(f"system: {spec.get('system_id')}  records={len(records)} labeled={len(labeled)} dropped_unlabeled={dropped} evaluated={len(evaluated)}")
    for key in ("action_recall", "action_precision", "surface_coverage", "boundary_accuracy", "abstention_accuracy", "illegal_action_rate", "jev_accuracy", "replacement_rate"):
        print(f"  {key:22} {fmt(metrics[key])}")
    lat = metrics["latency_ms"]
    print(f"  {'latency_ms':22} mean={'n/a' if lat['mean'] is None else f'{lat['mean']:.1f}'} p95={'n/a' if lat['p95'] is None else f'{lat['p95']:.1f}'}")
    print(f"  {'usage':22} {metrics['usage'] or 'n/a'}")
    print(f"  {'deterministic':22} {fmt(metrics['deterministic'])} decided by code, agreement with labels (must be 100%)")
    if deterministic and det_agree != len(deterministic):
        failures.append(f"deterministic decisions disagree with labels: {len(deterministic) - det_agree} (host rule bug, not a model issue)")
        approved = False
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
                print(f"  {model_name:28} {len(subset):4d}  {fmt(m['jev_accuracy']):>14}  {fmt(m['boundary_accuracy']):>12}  {fmt(m['abstention_accuracy']):>12}  {m['illegal_action_rate']['count']:8d}  {'n/a' if p95 is None else f'{p95:.0f}':>7}")
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
