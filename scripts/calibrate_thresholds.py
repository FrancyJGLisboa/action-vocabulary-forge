#!/usr/bin/env python3
"""Suggest (and optionally write) per-question and per-action confidence thresholds.

Reads the decision log plus ground-truth labels, keeps the training split only,
and picks for every group the lowest confidence bin whose cumulative accuracy
stays above --min-accuracy. Groups without enough samples stay uncalibrated,
which the generated adapter treats as "abstain".
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_history import (  # noqa: E402
    MIN_THRESHOLD, attach_labels, calibrate_groups, print_calibration_table, label_source_counts, load_questions, question_fallbacks, read_labels,
    read_log, split_cases,
)


def calibrate(
    bundle: Path,
    records: list[dict[str, Any]],
    *,
    min_accuracy: float = 0.97,
    min_samples: int = 30,
    min_threshold: float = MIN_THRESHOLD,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (policy_block, table_rows) from labeled training records."""
    spec, questions = load_questions(bundle)
    policy: dict[str, Any] = dict(spec.get("policy") or {})
    policy.setdefault("default_when_uncalibrated", "abstain")
    policy["min_accuracy"] = min_accuracy
    policy["min_threshold"] = min_threshold
    fallbacks = {qid: question_fallbacks(q) for qid, q in questions.items()}
    scoped, rows = calibrate_groups(records, fallbacks, min_accuracy=min_accuracy, min_samples=min_samples,
                                    min_threshold=min_threshold)
    if scoped:
        policy["questions"] = scoped
    return policy, rows


print_table = print_calibration_table


def write_policy(bundle: Path, policy: dict[str, Any], source: str) -> None:
    path = bundle / "jev_adapter_spec.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spec["policy"] = {**policy, "calibrated_at": datetime.now(timezone.utc).isoformat(), "calibration_source": source}
    path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--min-accuracy", type=float, default=0.97)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--min-threshold", type=float, default=MIN_THRESHOLD,
                        help=f"floor for a calibrated threshold (default {MIN_THRESHOLD}); a lower suggestion is raised to it")
    parser.add_argument("--heldout-fraction", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--write", action="store_true", help="rewrite policy in jev_adapter_spec.yaml")
    args = parser.parse_args(argv)

    records = read_log(args.log)
    labeled, dropped = attach_labels(records, read_labels(args.labels, with_source=True))
    train, heldout = split_cases(labeled, args.heldout_fraction, args.seed)
    if not train:
        print("no history: policy stays uncalibrated; every non-fallback action abstains until calibrated")
        if args.write:
            write_policy(args.bundle, {"default_when_uncalibrated": "abstain"}, str(args.log))
            print(f"wrote policy.default_when_uncalibrated: abstain to {args.bundle / 'jev_adapter_spec.yaml'}")
        return 0
    print(f"records={len(records)} labeled={len(labeled)} dropped_unlabeled={dropped} train={len(train)} heldout={len(heldout)}")
    print(f"train label sources: {label_source_counts(train)} (the release gate judges human labels only)")
    policy, rows = calibrate(args.bundle, train, min_accuracy=args.min_accuracy, min_samples=args.min_samples,
                             min_threshold=args.min_threshold)
    print_table(rows)
    print("\npolicy:")
    print(yaml.safe_dump(policy, sort_keys=False).rstrip())
    if args.write:
        write_policy(args.bundle, policy, str(args.log))
        print(f"\nwrote policy to {args.bundle / 'jev_adapter_spec.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
