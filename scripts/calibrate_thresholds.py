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
    attach_labels, calibration_bins, load_questions, question_fallbacks, read_labels, read_log, split_cases, suggest_threshold,
)


def calibrate(
    bundle: Path,
    records: list[dict[str, Any]],
    *,
    min_accuracy: float = 0.97,
    min_samples: int = 30,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (policy_block, table_rows) from labeled training records."""
    spec, questions = load_questions(bundle)
    policy: dict[str, Any] = dict(spec.get("policy") or {})
    policy.setdefault("default_when_uncalibrated", "abstain")
    policy["min_accuracy"] = min_accuracy
    scoped: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for question_id, question in questions.items():
        fallbacks = question_fallbacks(question)
        scoped_entry: dict[str, Any] = {}
        subset = [r for r in records if r.get("question_id") == question_id and r.get("proposed_action_id") not in fallbacks]
        groups: list[tuple[str, str | None, list[dict[str, Any]]]] = [(question_id, None, subset)]
        for action_id in sorted({r.get("proposed_action_id") for r in subset if r.get("proposed_action_id")}):
            groups.append((question_id, action_id, [r for r in subset if r.get("proposed_action_id") == action_id]))
        for qid, action_id, group in groups:
            bins = calibration_bins(group)
            suggested = suggest_threshold(bins, min_accuracy) if len(group) >= min_samples else None
            rows.append({"question_id": qid, "action_id": action_id, "n": len(group), "bins": bins, "suggested": suggested})
            if suggested is None:
                continue
            if action_id is None:
                scoped_entry["min_confidence"] = suggested
            else:
                scoped_entry.setdefault("actions", {})[action_id] = suggested
        if scoped_entry:
            scoped[question_id] = scoped_entry
    if scoped:
        policy["questions"] = scoped
    return policy, rows


def print_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'question':32} {'action':28} {'n':>5}  {'suggested':>9}  bins (n/acc)")
    for row in rows:
        def _acc(cell: dict[str, Any]) -> str:
            return "-" if cell["acc"] is None else format(cell["acc"], ".2f")

        bins = " ".join(
            "{}:{}/{}".format(key, cell["n"], _acc(cell))
            for key, cell in row["bins"].items() if cell["n"]
        )
        suggested = "-" if row["suggested"] is None else f"{row['suggested']:.2f}"
        print(f"{row['question_id']:32} {(row['action_id'] or '(question)'):28} {row['n']:5d}  {suggested:>9}  {bins}")


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
    parser.add_argument("--heldout-fraction", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--write", action="store_true", help="rewrite policy in jev_adapter_spec.yaml")
    args = parser.parse_args(argv)

    records = read_log(args.log)
    labeled, dropped = attach_labels(records, read_labels(args.labels))
    train, heldout = split_cases(labeled, args.heldout_fraction, args.seed)
    if not train:
        print("no history: policy stays uncalibrated; every non-fallback action abstains until calibrated")
        if args.write:
            write_policy(args.bundle, {"default_when_uncalibrated": "abstain"}, str(args.log))
            print(f"wrote policy.default_when_uncalibrated: abstain to {args.bundle / 'jev_adapter_spec.yaml'}")
        return 0
    print(f"records={len(records)} labeled={len(labeled)} dropped_unlabeled={dropped} train={len(train)} heldout={len(heldout)}")
    policy, rows = calibrate(args.bundle, train, min_accuracy=args.min_accuracy, min_samples=args.min_samples)
    print_table(rows)
    print("\npolicy:")
    print(yaml.safe_dump(policy, sort_keys=False).rstrip())
    if args.write:
        write_policy(args.bundle, policy, str(args.log))
        print(f"\nwrote policy to {args.bundle / 'jev_adapter_spec.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
