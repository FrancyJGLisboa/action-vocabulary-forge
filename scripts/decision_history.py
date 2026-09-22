"""Shared helpers for reading, labeling and splitting the decision log."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

CALIBRATION_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
LABEL_FIELDS = ("case_id", "question_id", "ground_truth_action_id")


def read_log(path: Path | str) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{line_number}: invalid JSON ({exc})") from exc
        if isinstance(item, dict):
            records.append(item)
    return records


def read_labels(path: Path | str | None) -> dict[tuple[str, str], str]:
    """JSONL or CSV with case_id, question_id, ground_truth_action_id."""
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"labels file not found: {path}")
    rows: Iterable[Mapping[str, Any]]
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    labels: dict[tuple[str, str], str] = {}
    for row in rows:
        if not all(row.get(field) for field in LABEL_FIELDS):
            raise SystemExit(f"labels: every row needs {LABEL_FIELDS}")
        labels[(str(row["case_id"]), str(row["question_id"]))] = str(row["ground_truth_action_id"])
    return labels


def attach_labels(records: list[dict[str, Any]], labels: Mapping[tuple[str, str], str]) -> tuple[list[dict[str, Any]], int]:
    """Inline ground_truth_action_id wins; unlabeled records are dropped and counted."""
    labeled = []
    dropped = 0
    for record in records:
        truth = record.get("ground_truth_action_id")
        if not truth:
            truth = labels.get((str(record.get("case_id")), str(record.get("question_id"))))
        if not truth:
            dropped += 1
            continue
        labeled.append({**record, "ground_truth_action_id": truth})
    return labeled, dropped


def split_cases(records: list[dict[str, Any]], heldout_fraction: float, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic split by case_id; a case never lands in both halves."""
    train, heldout = [], []
    for record in records:
        digest = hashlib.sha256(f"{seed}:{record.get('case_id')}".encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        (heldout if bucket < heldout_fraction else train).append(record)
    return train, heldout


def bin_label(lo: float, hi: float) -> str:
    return f"{lo:g}-{hi:g}"


def is_deterministic(record: Mapping[str, Any]) -> bool:
    """A record the host decided without the model (single legal action, adapter error, ...)."""
    return str(record.get("reason") or "").startswith("deterministic:") or record.get("confidence") is None


def calibration_bins(records: Iterable[Mapping[str, Any]], edges: tuple[float, ...] = CALIBRATION_EDGES) -> dict[str, dict[str, Any]]:
    """{bin: {n, acc}} where a hit is proposed_action_id == ground_truth_action_id.

    Records without a confidence (deterministic decisions) are skipped: they carry no
    model signal and would land in the lowest bin as hits, dragging thresholds down.
    """
    bins = {bin_label(lo, hi): {"n": 0, "hits": 0} for lo, hi in zip(edges, edges[1:])}
    for record in records:
        confidence = record.get("confidence")
        if confidence is None:
            continue
        value = float(confidence)
        for lo, hi in zip(edges, edges[1:]):
            if lo <= value < hi or (value >= hi and hi == edges[-1]):
                cell = bins[bin_label(lo, hi)]
                cell["n"] += 1
                cell["hits"] += int(record.get("proposed_action_id") == record.get("ground_truth_action_id"))
                break
    return {
        key: {"n": cell["n"], "acc": (cell["hits"] / cell["n"]) if cell["n"] else None}
        for key, cell in bins.items()
    }


def suggest_threshold(bins: Mapping[str, Mapping[str, Any]], min_acc: float = 0.97) -> float | None:
    """Lowest bin floor whose cumulative accuracy from the top down stays >= min_acc."""
    ordered = sorted(
        ((float(key.split("-")[0]), cell["n"], (cell["acc"] or 0.0) * cell["n"]) for key, cell in bins.items() if cell["n"]),
        reverse=True,
    )
    n = hits = 0.0
    best = None
    for lo, count, hit in ordered:
        n += count
        hits += hit
        if n and hits / n >= min_acc:
            best = lo
        else:
            break
    return best


def load_questions(bundle: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    spec = yaml.safe_load((bundle / "jev_adapter_spec.yaml").read_text(encoding="utf-8")) or {}
    questions = {q["question_id"]: q for q in spec.get("classifier_questions", []) if isinstance(q, dict) and q.get("question_id")}
    return spec, questions


def question_fallbacks(question: Mapping[str, Any]) -> set[str]:
    values = {question.get("abstention_action_id"), question.get("no_action_id"), question.get("abstention_choice")}
    if question.get("type", "choice") == "choice":
        for item in question.get("choices", []):
            if item.get("id") == question.get("abstention_choice"):
                values.add(item.get("executor_action_id"))
    return {value for value in values if value}


def question_executors(question: Mapping[str, Any]) -> set[str]:
    kind = question.get("type", "choice")
    if kind == "choice":
        result = {item.get("executor_action_id") for item in question.get("choices", [])}
        if question.get("dynamic_executor_action_id"):
            result.add(question["dynamic_executor_action_id"])
    elif kind == "noul":
        result = {question.get("yes_action_id"), question.get("no_action_id")}
    else:
        result = {item.get("executor_action_id") for item in question.get("levels", [])}
    return {value for value in result if value}
