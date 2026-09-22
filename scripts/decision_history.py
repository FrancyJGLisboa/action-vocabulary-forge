"""Shared helpers for reading, labeling and splitting the decision log."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping


CALIBRATION_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
LABEL_FIELDS = ("case_id", "question_id", "ground_truth_action_id")
# Only these label sources may judge the release gate. A label with no source counts as human,
# so label files written before label_source existed keep working; a machine labeler must say so.
HUMAN_LABEL_SOURCES = {"human"}
DEFAULT_LABEL_SOURCE = "human"


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


def read_labels(path: Path | str | None, *, with_source: bool = False) -> dict[tuple[str, str], Any]:
    """JSONL or CSV with case_id, question_id, ground_truth_action_id and optional label_source.

    With ``with_source`` each value is ``(ground_truth_action_id, label_source)``.
    """
    if path is None:
        return {}
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"labels file not found: {path}")
    rows: Iterable[Mapping[str, Any]]
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader([line for line in text.splitlines() if not line.lstrip().startswith("#")]))
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    labels: dict[tuple[str, str], str] = {}
    for row in rows:
        if not all(row.get(field) for field in LABEL_FIELDS):
            raise SystemExit(f"labels: every row needs {LABEL_FIELDS}")
        truth = str(row["ground_truth_action_id"])
        source = str(row.get("label_source") or DEFAULT_LABEL_SOURCE)
        labels[(str(row["case_id"]), str(row["question_id"]))] = (truth, source) if with_source else truth
    return labels


def attach_labels(records: list[dict[str, Any]], labels: Mapping[tuple[str, str], Any]) -> tuple[list[dict[str, Any]], int]:
    """Inline ground_truth_action_id wins; unlabeled records are dropped and counted.

    Every labeled record gets a ``label_source`` (inline, from the labels file, or the default).
    """
    labeled = []
    dropped = 0
    for record in records:
        truth = record.get("ground_truth_action_id")
        source = record.get("label_source")
        if not truth:
            found = labels.get((str(record.get("case_id")), str(record.get("question_id"))))
            truth, source = found if isinstance(found, tuple) else (found, None)
        if not truth:
            dropped += 1
            continue
        labeled.append({**record, "ground_truth_action_id": truth, "label_source": source or DEFAULT_LABEL_SOURCE})
    return labeled, dropped


def human_labeled(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The records a release gate may judge, and how many were set aside as machine-labeled."""
    kept = [r for r in records if r.get("label_source", DEFAULT_LABEL_SOURCE) in HUMAN_LABEL_SOURCES]
    return kept, len(records) - len(kept)


def label_source_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        source = str(record.get("label_source", DEFAULT_LABEL_SOURCE))
        counts[source] = counts.get(source, 0) + 1
    return counts


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
    import yaml  # only bundle readers need it; the bundle-free core does not

    spec = yaml.safe_load((bundle / "jev_adapter_spec.yaml").read_text(encoding="utf-8")) or {}
    questions = {q["question_id"]: q for q in spec.get("classifier_questions", []) if isinstance(q, dict) and q.get("question_id")}
    return spec, questions


def question_fallbacks(question: Mapping[str, Any]) -> set[str]:
    """Same rule as the generated adapter's question_fallbacks: a Noul's no_action_id is a
    fallback only when the question declares no abstention_action_id."""
    values = {question.get("abstention_action_id"), question.get("abstention_choice")}
    if not question.get("abstention_action_id"):
        values.add(question.get("no_action_id"))
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


# --- bundle-free core: calibration, metrics, gate -------------------------------------

MIN_THRESHOLD = 0.5


def ratio(count: int, total: int) -> dict[str, Any]:
    return {"count": count, "total": total, "pct": (count / total) if total else None}


def fmt(value: Mapping[str, Any]) -> str:
    pct = "n/a" if value["pct"] is None else f"{value['pct'] * 100:.1f}%"
    return f"{value['count']}/{value['total']} ({pct})"


def calibrate_groups(
    records: list[dict[str, Any]],
    fallbacks: Mapping[str, set[str]],
    *,
    min_accuracy: float = 0.97,
    min_samples: int = 30,
    min_threshold: float = MIN_THRESHOLD,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Per question and per proposed non-fallback action, the lowest safe confidence floor.

    ``fallbacks`` maps every question to calibrate onto the answers that escape a threshold.
    Returns ``(scoped, rows)``: the ``policy.questions`` block and one table row per group.
    """
    scoped: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for question_id, question_fallbacks_ in fallbacks.items():
        scoped_entry: dict[str, Any] = {}
        subset = [r for r in records if r.get("question_id") == question_id and r.get("proposed_action_id") not in question_fallbacks_]
        groups: list[tuple[str, str | None, list[dict[str, Any]]]] = [(question_id, None, subset)]
        for action_id in sorted({r.get("proposed_action_id") for r in subset if r.get("proposed_action_id")}):
            groups.append((question_id, action_id, [r for r in subset if r.get("proposed_action_id") == action_id]))
        for qid, action_id, group in groups:
            bins = calibration_bins(group)
            suggested = suggest_threshold(bins, min_accuracy) if len(group) >= min_samples else None
            clamped = False
            if suggested is not None and suggested < min_threshold:
                # The history says even the lowest band is accurate enough, which would leave the
                # action ungated. On a small sample that is overfitting, not a licence: a model's
                # top choice below this confidence is barely ahead of its runner-up.
                suggested, clamped = min_threshold, True
            rows.append({"question_id": qid, "action_id": action_id, "n": len(group), "bins": bins,
                         "suggested": suggested, "clamped": clamped})
            if suggested is None:
                continue
            if action_id is None:
                scoped_entry["min_confidence"] = suggested
            else:
                scoped_entry.setdefault("actions", {})[action_id] = suggested
        if scoped_entry:
            scoped[question_id] = scoped_entry
    return scoped, rows


def log_metrics(
    records: list[dict[str, Any]],
    fallbacks: Mapping[str, set[str]],
    registry_actions: set[str] | None = None,
) -> dict[str, Any]:
    """references/evaluation.md log metrics; action recall only when a registry is known."""
    truths = {r["ground_truth_action_id"] for r in records}
    recall = ratio(len(truths & registry_actions), len(truths)) if registry_actions is not None else ratio(0, 0)
    # An escalated record abstained at the model but executed a concluding action, so it is judged as a decision.
    escalated = [r for r in records if r.get("decided_by") == "escalation"]
    decided = [r for r in records if not r.get("abstained") or r.get("decided_by") == "escalation"]
    boundary = ratio(sum(1 for r in decided if r.get("action_id") == r["ground_truth_action_id"]), len(decided))
    need_abstain = [r for r in records if r["ground_truth_action_id"] in fallbacks.get(r.get("question_id"), set())]
    abstained_ok = sum(
        1 for r in need_abstain
        if (r.get("abstained") and r.get("decided_by") != "escalation") or r.get("action_id") in fallbacks.get(r.get("question_id"), set())
    )
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
        "escalation_accuracy": ratio(sum(1 for r in escalated if r.get("action_id") == r["ground_truth_action_id"]), len(escalated)),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p95": (sorted(latencies)[max(0, int(round(0.95 * len(latencies))) - 1)] if latencies else None),
        },
        "usage": {"input_tokens": usage_in, "output_tokens": usage_out} if (usage_in or usage_out) else None,
        "calibration": {qid: calibration_bins([r for r in records if r.get("question_id") == qid]) for qid in fallbacks},
    }


EMPTY_METRICS: dict[str, Any] = {
    "action_recall": ratio(0, 0), "boundary_accuracy": ratio(0, 0), "abstention_accuracy": ratio(0, 0),
    "illegal_action_rate": ratio(0, 0), "illegal_records": [], "jev_accuracy": ratio(0, 0), "escalation_accuracy": ratio(0, 0),
    "latency_ms": {"mean": None, "p95": None}, "usage": None, "calibration": {},
}


def threshold_failures(
    records: list[dict[str, Any]],
    fallbacks: Mapping[str, set[str]],
    policy: Mapping[str, Any],
    answers: Mapping[str, set[str]] | None = None,
) -> list[str]:
    """Every non-fallback answer seen in ``records`` needs a threshold, unless the policy allows it."""
    if policy.get("default_when_uncalibrated") == "allow" or policy.get("min_confidence") is not None:
        return []
    scoped = policy.get("questions") or {}
    failures = []
    for qid, question_fallbacks_ in fallbacks.items():
        seen = {r.get("proposed_action_id") for r in records if r.get("question_id") == qid}
        candidates = (answers or {}).get(qid, seen) - question_fallbacks_
        for action_id in sorted(a for a in candidates if a in seen):
            entry = scoped.get(qid) or {}
            if (entry.get("actions") or {}).get(action_id) is None and entry.get("min_confidence") is None:
                failures.append(f"question {qid} action {action_id}: no calibrated threshold (uncalibrated => abstain)")
    return failures


def gate_failures(
    metrics: Mapping[str, Any],
    records: list[dict[str, Any]],
    *,
    min_boundary: float,
    min_abstention: float,
    min_samples: int,
) -> list[str]:
    """The release-gate checks that need no bundle."""
    failures: list[str] = []
    if metrics["illegal_action_rate"]["count"]:
        failures.append(f"illegal actions on held-out: {metrics['illegal_action_rate']['count']} (cases {metrics['illegal_records'][:5]})")
    if metrics["boundary_accuracy"]["pct"] is not None and metrics["boundary_accuracy"]["pct"] < min_boundary:
        failures.append(f"boundary accuracy {fmt(metrics['boundary_accuracy'])} below {min_boundary:.0%}")
    if metrics["abstention_accuracy"]["pct"] is not None and metrics["abstention_accuracy"]["pct"] < min_abstention:
        failures.append(f"abstention accuracy {fmt(metrics['abstention_accuracy'])} below {min_abstention:.0%}")
    if len(records) < min_samples:
        failures.append(f"held-out has {len(records)} records, fewer than {min_samples}")
    deterministic = [r for r in records if is_deterministic(r)]
    disagree = sum(1 for r in deterministic if r.get("action_id") != r.get("ground_truth_action_id"))
    if disagree:
        failures.append(f"deterministic decisions disagree with labels: {disagree} (host rule bug, not a model issue)")
    return failures


def print_calibration_table(rows: list[dict[str, Any]]) -> None:
    """A * marks a threshold raised to the floor because the history suggested a lower one."""
    print(f"{'question':32} {'action':28} {'n':>5}  {'suggested':>9}  bins (n/acc)")
    for row in rows:
        def _acc(cell: Mapping[str, Any]) -> str:
            return "-" if cell["acc"] is None else format(cell["acc"], ".2f")

        bins = " ".join(
            "{}:{}/{}".format(key, cell["n"], _acc(cell))
            for key, cell in row["bins"].items() if cell["n"]
        )
        suggested = "-" if row["suggested"] is None else (format(row["suggested"], ".2f") + ("*" if row.get("clamped") else ""))
        print(f"{row['question_id']:32} {(row['action_id'] or '(question)'):28} {row['n']:5d}  {suggested:>9}  {bins}")
