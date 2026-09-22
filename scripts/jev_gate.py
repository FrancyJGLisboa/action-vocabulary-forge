#!/usr/bin/env python3
"""Calibrate thresholds and run the release gate for any JEV (or Laya) decision log, no bundle needed.

One JSONL record per decision:

    {"case_id": "t-81", "question_id": "triage", "answer": "billing", "confidence": 0.93,
     "truth": "billing", "label_source": "human"}

Instead of ``answer``/``confidence`` a record may carry the raw per-question answer as ``jev``
(``{"choice": ..., "confidence": ...}``, ``{"noul": 0.8}`` or ``{"score": 2}``). Optional fields:
``legal`` (answers allowed for this case), ``latency_ms``, ``usage``, ``model``. Labels may also
come from ``--labels`` (JSONL/CSV with case_id, question_id, ground_truth_action_id, label_source).

    jev_gate.py calibrate decisions.jsonl --abstain human --out thresholds.json
    jev_gate.py evaluate  decisions.jsonl --abstain human --thresholds thresholds.json

``calibrate`` uses the training split; ``evaluate`` replays the thresholds on the held-out split,
judged on human labels only, and prints ``RELEASE GATE: APPROVE | HOLD`` (exit 0 | 2). An answer
below its threshold becomes the question's abstain value (``--abstain``, default ``abstain``); a
truth equal to that value means "a person should have taken this one".
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_history import (  # noqa: E402
    EMPTY_METRICS, MIN_THRESHOLD, attach_labels, calibrate_groups, fmt, gate_failures, human_labeled, is_deterministic,
    label_source_counts, log_metrics, print_calibration_table, read_labels, read_log, split_cases, threshold_failures,
)

DEFAULT_ABSTAIN = "abstain"
UNCALIBRATED = -1.0


def jev_answer(answer: Mapping[str, Any]) -> tuple[str, float | None]:
    """A raw System One answer as (answer, confidence). Noul: yes at >= 0.5; Score: the rounded level."""
    if "choice" in answer:
        confidence = answer.get("confidence")
        return str(answer["choice"]), None if confidence is None else float(confidence)
    if "noul" in answer:
        value = float(answer["noul"])
        return ("yes" if value >= 0.5 else "no"), max(value, 1.0 - value)
    if "score" in answer:
        confidence = answer.get("confidence")
        return str(round(float(answer["score"]))), None if confidence is None else float(confidence)
    raise SystemExit(f"unrecognised JEV answer (need choice, noul or score): {answer}")


def normalize(record: Mapping[str, Any]) -> dict[str, Any]:
    """A gate record in the field names the shared core reads."""
    for field in ("case_id", "question_id"):
        if not record.get(field):
            raise SystemExit(f"every record needs {field}: {record}")
    if "jev" in record:
        answer, confidence = jev_answer(record["jev"])
    elif "answer" in record:
        answer, confidence = str(record["answer"]), record.get("confidence")
    else:
        raise SystemExit(f"record {record['case_id']} has neither answer nor jev")
    out = {
        "case_id": str(record["case_id"]),
        "question_id": str(record["question_id"]),
        "proposed_action_id": answer,
        "action_id": answer,
        "confidence": None if confidence is None else float(confidence),
        "latency_ms": record.get("latency_ms"),
        "usage": record.get("usage"),
        "model": record.get("model"),
    }
    if record.get("legal") is not None:
        out["legal_actions"] = [str(item) for item in record["legal"]]
    if record.get("truth") is not None:
        out["ground_truth_action_id"] = str(record["truth"])
    if record.get("label_source") is not None:
        out["label_source"] = record["label_source"]
    return out


def parse_abstain(values: list[str], question_ids: set[str], base: Mapping[str, str] | None = None) -> dict[str, str]:
    """``--abstain VALUE`` for every question, ``--abstain QID=VALUE`` for one, applied in order over ``base``."""
    result = {qid: (base or {}).get(qid, DEFAULT_ABSTAIN) for qid in question_ids}
    for value in values:
        if "=" in value:
            qid, answer = value.split("=", 1)
            result[qid] = answer
        else:
            result = {qid: value for qid in question_ids}
    return result


def load(path: Path, labels: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    raw = read_log(path)
    records = [normalize(r) for r in raw]
    labeled, dropped = attach_labels(records, read_labels(labels, with_source=True))
    return records, labeled, dropped


def threshold_for(policy: Mapping[str, Any], question_id: str, answer: str, fallback: str) -> float | None:
    """Same precedence as the generated adapter: action -> question -> global -> uncalibrated."""
    if answer == fallback:
        return 0.0
    scoped = (policy.get("questions") or {}).get(question_id) or {}
    for value in ((scoped.get("actions") or {}).get(answer), scoped.get("min_confidence"), policy.get("min_confidence")):
        if value is not None:
            return float(value)
    return None if policy.get("default_when_uncalibrated") == "allow" else UNCALIBRATED


def replay(record: dict[str, Any], policy: Mapping[str, Any], fallback: str) -> dict[str, Any]:
    """What the policy would have done with this answer: act on it, or abstain."""
    if is_deterministic(record):
        return {**record, "abstained": False}
    threshold = threshold_for(policy, record["question_id"], record["proposed_action_id"], fallback)
    if threshold is None or (threshold != UNCALIBRATED and record["confidence"] >= threshold):
        return {**record, "abstained": False, "threshold": threshold}
    return {**record, "action_id": fallback, "abstained": True, "threshold": None if threshold == UNCALIBRATED else threshold}


def cmd_calibrate(args: argparse.Namespace) -> int:
    records, labeled, dropped = load(args.log, args.labels)
    abstain = parse_abstain(args.abstain, {r["question_id"] for r in records})
    train, heldout = split_cases(labeled, args.heldout_fraction, args.seed)
    print(f"records={len(records)} labeled={len(labeled)} dropped_unlabeled={dropped} train={len(train)} heldout={len(heldout)}")
    print(f"train label sources: {label_source_counts(train)} (the release gate judges human labels only)")
    scoped, rows = calibrate_groups(train, {qid: {value} for qid, value in abstain.items()}, min_accuracy=args.min_accuracy,
                                    min_samples=args.min_samples, min_threshold=args.min_threshold)
    print_calibration_table(rows)
    policy: dict[str, Any] = {
        "default_when_uncalibrated": "abstain",
        "min_accuracy": args.min_accuracy,
        "min_threshold": args.min_threshold,
        "abstain": abstain,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "calibration_source": str(args.log),
    }
    if scoped:
        policy["questions"] = scoped
    args.out.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    policy = json.loads(args.thresholds.read_text(encoding="utf-8")) if args.thresholds else {"default_when_uncalibrated": "abstain"}
    records, labeled, dropped = load(args.log, args.labels)
    question_ids = {r["question_id"] for r in records}
    abstain = parse_abstain(args.abstain, question_ids, base=policy.get("abstain"))
    fallbacks = {qid: {abstain[qid]} for qid in question_ids}
    evaluated = labeled if args.all else split_cases(labeled, args.heldout_fraction, args.seed)[1]
    # The gate never grades against machine labels: an LLM that labels (or escalates) would grade itself.
    evaluated, machine_labeled = human_labeled(evaluated)
    evaluated = [replay(r, policy, abstain[r["question_id"]]) for r in evaluated]
    metrics = log_metrics(evaluated, fallbacks) if evaluated else dict(EMPTY_METRICS)
    failures = gate_failures(metrics, evaluated, min_boundary=args.min_boundary_accuracy,
                             min_abstention=args.min_abstention_accuracy, min_samples=args.min_samples)
    failures += threshold_failures(evaluated, fallbacks, policy)
    approved = not failures
    acted = sum(1 for r in evaluated if not r.get("abstained"))

    print(f"records={len(records)} labeled={len(labeled)} dropped_unlabeled={dropped} "
          f"excluded_machine_labeled={machine_labeled} evaluated={len(evaluated)}")
    print(f"  {'model_accuracy':22} {fmt(metrics['jev_accuracy'])} raw answer == truth")
    print(f"  {'acted_accuracy':22} {fmt(metrics['boundary_accuracy'])} answers above threshold that were right")
    print(f"  {'abstention_accuracy':22} {fmt(metrics['abstention_accuracy'])} cases a person should take that abstained")
    print(f"  {'automation_rate':22} {acted}/{len(evaluated)} acted without a person")
    print(f"  {'illegal_answer_rate':22} {fmt(metrics['illegal_action_rate'])}")
    print()
    print("RELEASE GATE: " + ("APPROVE" if approved else "HOLD"))
    for failure in failures:
        print(f"  - {failure}")
    if args.json:
        payload = {"approved": approved, "failures": failures, "evaluated": len(evaluated), "acted": acted,
                   "metrics": {k: v for k, v in metrics.items() if k not in ("calibration", "action_recall", "escalation_accuracy")}}
        args.json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return 0 if approved else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("calibrate", "evaluate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("log", type=Path)
        cmd.add_argument("--labels", type=Path, default=None)
        cmd.add_argument("--abstain", action="append", default=[], metavar="[QID=]VALUE",
                         help=f"the answer that means 'a person takes it' (default {DEFAULT_ABSTAIN!r})")
        cmd.add_argument("--heldout-fraction", type=float, default=0.3)
        cmd.add_argument("--seed", type=int, default=7)
        cmd.add_argument("--min-samples", type=int, default=30)
    cal = sub.choices["calibrate"]
    cal.add_argument("--min-accuracy", type=float, default=0.97)
    cal.add_argument("--min-threshold", type=float, default=MIN_THRESHOLD)
    cal.add_argument("--out", type=Path, default=Path("thresholds.json"))
    ev = sub.choices["evaluate"]
    ev.add_argument("--thresholds", type=Path, default=None, help="from calibrate; without it every answer abstains")
    ev.add_argument("--all", action="store_true", help="evaluate every labeled record instead of the held-out split")
    ev.add_argument("--min-boundary-accuracy", type=float, default=0.9)
    ev.add_argument("--min-abstention-accuracy", type=float, default=0.9)
    ev.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    return cmd_calibrate(args) if args.command == "calibrate" else cmd_evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
