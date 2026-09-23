#!/usr/bin/env python3
"""Discover and rank bounded decision surfaces from material and resolved cases.

The command is intentionally offline. It inventories source metadata, validates
historical case records, measures a deterministic text baseline on a disjoint
holdout, and scaffolds a non-production Semantic Decision Bundle for the
highest-ranked eligible surface. Historical actions are observations, not proof
of legality, policy correctness, or executable bindings.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import init_semantic_bundle  # noqa: E402
import validate_semantic_bundle  # noqa: E402


TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".csv", ".go", ".html", ".java", ".js",
    ".json", ".jsx", ".md", ".php", ".py", ".rb", ".rst", ".rs", ".sql",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
IGNORED_PARTS = {
    ".git", ".hg", ".mypy_cache", ".pomelo", ".pytest_cache", ".ruff_cache",
    ".svn", ".venv", "__pycache__", "build", "dist", "node_modules", "venv",
}
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9_'-]{2,}", re.UNICODE)
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "da", "das", "de", "do",
    "dos", "e", "for", "from", "in", "is", "it", "na", "no", "of", "on", "or",
    "para", "por", "que", "the", "this", "to", "um", "uma", "with",
}


class DiscoveryError(ValueError):
    """Input cannot support a trustworthy discovery run."""


@dataclass(frozen=True)
class Case:
    case_id: str
    surface: str
    action_label: str
    action_id: str
    text: str
    outcome_present: bool


def slug(value: Any, *, fallback: str = "item", limit: int = 64) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    text = text or fallback
    if text[0].isdigit():
        text = f"{fallback}_{text}"
    return text[:limit].rstrip("_") or fallback


def _lookup(record: Mapping[str, Any], path: str | None) -> Any:
    if not path:
        return None
    if path in record:
        return record[path]
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (Mapping, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if value is None:
        return ""
    return str(value).strip()


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise DiscoveryError(f"cases file does not exist: {path}")
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        records = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DiscoveryError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise DiscoveryError(f"{path}:{line_number}: each record must be an object")
            records.append(item)
        return records
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise DiscoveryError("cases file must be .jsonl, .ndjson, or .csv")


def _unique_ids(labels: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    used: dict[str, str] = {}
    for label in sorted(set(labels)):
        base = slug(label, fallback="action")
        action_id = base
        if action_id in used and used[action_id] != label:
            digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
            action_id = f"{base[:54]}_{digest}"
        used[action_id] = label
        result[label] = action_id
    return result


def load_taxonomy_descriptions(path: Path, labels: Iterable[str]) -> dict[str, str]:
    """Read exact, source-backed label descriptions; ambiguous shapes are ignored."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.suffix.lower() == ".json" else yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError):
        return {}
    mappings = []
    if isinstance(raw, Mapping):
        mappings.append(raw)
        for key in ("labels", "intents", "taxonomy", "actions"):
            if isinstance(raw.get(key), Mapping):
                mappings.append(raw[key])
    wanted = set(labels)
    found: dict[str, str] = {}
    for mapping in mappings:
        claims: dict[str, list[str]] = defaultdict(list)
        for label in wanted:
            value = mapping.get(label)
            if isinstance(value, str) and value.strip():
                claims[value.strip()].append(label)
                if label in found and found[label] != value.strip():
                    found.pop(label, None)
                elif label not in found:
                    found[label] = value.strip()
        for value, claim_labels in claims.items():
            if len(claim_labels) > 1:
                for label in claim_labels:
                    found.pop(label, None)
    return found


def load_cases(
    path: Path,
    *,
    case_id_field: str,
    text_field: str,
    action_field: str,
    outcome_field: str | None,
    surface_field: str | None,
) -> list[Case]:
    records = _load_records(path)
    if not records:
        raise DiscoveryError("cases file contains no records")
    raw: list[tuple[str, str, str, str, bool]] = []
    seen: set[str] = set()
    for index, record in enumerate(records, 1):
        case_id = _text(_lookup(record, case_id_field))
        action = _text(_lookup(record, action_field))
        surface = _text(_lookup(record, surface_field)) if surface_field else "default_surface"
        if not case_id:
            raise DiscoveryError(f"record {index}: missing non-empty {case_id_field!r}")
        if case_id in seen:
            raise DiscoveryError(f"record {index}: duplicate case ID {case_id!r}")
        if not action:
            raise DiscoveryError(f"record {index}: missing non-empty {action_field!r}")
        if not surface:
            raise DiscoveryError(f"record {index}: missing non-empty {surface_field!r}")
        seen.add(case_id)
        context = _text(_lookup(record, text_field))
        outcome = _lookup(record, outcome_field)
        raw.append((case_id, surface, action, context, outcome not in (None, "")))

    action_ids = _unique_ids(item[2] for item in raw)
    surface_ids = _unique_ids(item[1] for item in raw)
    return [
        Case(case_id, surface_ids[surface], action, action_ids[action], context, outcome)
        for case_id, surface, action, context, outcome in raw
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".rst", ".txt", ".pdf", ".doc", ".docx"}:
        return "document"
    if suffix in {".csv", ".json", ".jsonl", ".ndjson"}:
        return "dataset"
    if suffix in TEXT_EXTENSIONS:
        return "code"
    return "other"


def inventory_sources(paths: Iterable[Path], cases_path: Path, *, max_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    files: dict[Path, Path] = {}
    for supplied in paths:
        path = supplied.expanduser().resolve()
        if not path.exists():
            raise DiscoveryError(f"source does not exist: {supplied}")
        candidates = [path] if path.is_file() else path.rglob("*")
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file() or any(part in IGNORED_PARTS for part in candidate.parts):
                continue
            try:
                if candidate.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            files[candidate.resolve()] = supplied
    files[cases_path.resolve()] = cases_path
    result = []
    for path in sorted(files, key=lambda item: str(item)):
        digest = _sha256(path)
        is_cases = path == cases_path.resolve()
        source_hash = hashlib.sha256((str(path) + "\0" + digest).encode("utf-8")).hexdigest()
        result.append(
            {
                "source_id": ("cases" if is_cases else "material") + f":{source_hash[:16]}",
                "kind": "dataset" if is_cases else _source_kind(path),
                "locator": str(path),
                "authority": "operational" if is_cases else "contextual",
                "included": True,
                "sha256": digest,
                "bytes": path.stat().st_size,
            }
        )
    return result


def _tokens(text: str) -> list[str]:
    return [token for token in (item.lower() for item in TOKEN_RE.findall(text)) if token not in STOP_WORDS]


def stratified_split(cases: list[Case], fraction: float) -> tuple[list[Case], list[Case]]:
    by_action: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        by_action[case.action_id].append(case)
    train: list[Case] = []
    test: list[Case] = []
    for action_cases in by_action.values():
        ordered = sorted(
            action_cases,
            key=lambda item: hashlib.sha256(item.case_id.encode("utf-8")).hexdigest(),
        )
        if len(ordered) < 2:
            train.extend(ordered)
            continue
        test_count = max(1, round(len(ordered) * fraction))
        test_count = min(test_count, len(ordered) - 1)
        test.extend(ordered[:test_count])
        train.extend(ordered[test_count:])
    return train, test


class NaiveBayes:
    def __init__(self) -> None:
        self.class_docs: Counter[str] = Counter()
        self.class_tokens: dict[str, Counter[str]] = defaultdict(Counter)
        self.class_totals: Counter[str] = Counter()
        self.vocabulary: set[str] = set()

    def fit(self, cases: Iterable[Case]) -> None:
        for case in cases:
            tokens = _tokens(case.text)
            self.class_docs[case.action_id] += 1
            self.class_tokens[case.action_id].update(tokens)
            self.class_totals[case.action_id] += len(tokens)
            self.vocabulary.update(tokens)

    def predict(self, text: str) -> tuple[str | None, float]:
        if not self.class_docs or not self.vocabulary or not _tokens(text):
            return None, 0.0
        total_docs = sum(self.class_docs.values())
        vocab_size = len(self.vocabulary)
        scores: dict[str, float] = {}
        for action_id, docs in self.class_docs.items():
            score = math.log(docs / total_docs)
            denominator = self.class_totals[action_id] + vocab_size
            counts = self.class_tokens[action_id]
            for token in _tokens(text):
                score += math.log((counts[token] + 1) / denominator)
            scores[action_id] = score
        best = max(scores, key=scores.get)
        peak = scores[best]
        confidence = 1.0 / sum(math.exp(value - peak) for value in scores.values())
        return best, confidence


def baseline(train: list[Case], test: list[Case], threshold: float) -> dict[str, Any]:
    if not train or not test:
        return {"status": "unavailable", "reason": "no disjoint train/holdout split"}
    train_actions = {case.action_id for case in train}
    test_actions = {case.action_id for case in test}
    if not test_actions.issubset(train_actions):
        return {"status": "unavailable", "reason": "a holdout action is absent from training"}
    majority = Counter(case.action_id for case in train).most_common(1)[0][0]
    majority_accuracy = sum(case.action_id == majority for case in test) / len(test)
    model = NaiveBayes()
    model.fit(train)
    predictions = [(*model.predict(case.text), case.action_id) for case in test]
    available = [(predicted, confidence, actual) for predicted, confidence, actual in predictions if predicted]
    if not available:
        return {"status": "unavailable", "reason": "holdout context contains no usable text"}
    correct = sum(predicted == actual for predicted, _, actual in available)
    confident = [item for item in available if item[1] >= threshold]
    return {
        "status": "measured_holdout",
        "model": "local_multinomial_naive_bayes",
        "not_jev_performance": True,
        "train_cases": len(train),
        "holdout_cases": len(test),
        "prediction_coverage": round(len(available) / len(test), 4),
        "majority_accuracy": round(majority_accuracy, 4),
        "accuracy": round(correct / len(available), 4),
        "confidence_threshold": threshold,
        "confident_coverage": round(len(confident) / len(test), 4),
        "confident_accuracy": round(
            sum(predicted == actual for predicted, _, actual in confident) / len(confident), 4
        ) if confident else None,
        "illegal_predictions": 0,
    }


def analyze_surface(
    surface_id: str,
    cases: list[Case],
    *,
    min_cases: int,
    min_action_cases: int,
    max_actions: int,
    test_fraction: float,
    confidence_threshold: float,
) -> tuple[dict[str, Any], list[Case], list[Case]]:
    counts = Counter(case.action_id for case in cases)
    labels = {case.action_id: case.action_label for case in cases}
    total = len(cases)
    context_coverage = sum(bool(case.text) for case in cases) / total
    outcome_coverage = sum(case.outcome_present for case in cases) / total
    train, test = stratified_split(cases, test_fraction)
    measured = baseline(train, test, confidence_threshold)
    failures = []
    if total < min_cases:
        failures.append(f"needs at least {min_cases} cases; found {total}")
    if not 2 <= len(counts) <= max_actions:
        failures.append(f"needs 2-{max_actions} bounded actions; found {len(counts)}")
    weak = {action: count for action, count in counts.items() if count < min_action_cases}
    if weak:
        failures.append(f"actions below {min_action_cases} cases: {', '.join(sorted(weak))}")
    if context_coverage < 0.8:
        failures.append(f"context coverage {context_coverage:.0%} is below 80%")
    if measured["status"] != "measured_holdout":
        failures.append(f"holdout baseline unavailable: {measured['reason']}")

    bounded_score = 20 if 2 <= len(counts) <= max_actions else 0
    score = (
        25 * min(total / max(min_cases, 1), 1)
        + bounded_score
        + 20 * min((min(counts.values()) if counts else 0) / max(min_action_cases, 1), 1)
        + 10 * context_coverage
        + 10 * outcome_coverage
        + (15 if measured["status"] == "measured_holdout" else 0)
    )
    action_rows = [
        {"action_id": action_id, "observed_label": labels[action_id], "case_count": count}
        for action_id, count in sorted(counts.items())
    ]
    hypotheses = [
        {
            "judgment_id": f"case_supports_{action_id}",
            "type": "noul",
            "instructions": f"Does the supplied case evidence support the historical resolution {labels[action_id]!r}?",
            "maturity": "candidate",
            "requires_domain_review": True,
        }
        for action_id in sorted(counts)
    ]
    candidate = {
        "surface_id": surface_id,
        "readiness_score": round(score, 2),
        "status": "candidate_for_human_review" if not failures else "insufficient_evidence",
        "review_required": True,
        "case_count": total,
        "action_count": len(counts),
        "actions": action_rows,
        "context_coverage": round(context_coverage, 4),
        "outcome_coverage": round(outcome_coverage, 4),
        "baseline": measured,
        "judgment_hypotheses": hypotheses,
        "blocking_gaps": failures,
        "authority_note": "Historical labels describe observed behavior; they do not establish policy, legality, or executable bindings.",
    }
    return candidate, train, test


def _case_evidence_id(surface_id: str, action_id: str) -> str:
    return slug(f"cases_{surface_id}_{action_id}", fallback="cases")


def candidate_bundle(
    system_id: str,
    scope: str,
    candidate: Mapping[str, Any],
    sources: list[dict[str, Any]],
    cases_source_id: str,
    taxonomy_descriptions: Mapping[str, str] | None = None,
    taxonomy_source_id: str | None = None,
) -> dict[str, Any]:
    surface_id = candidate["surface_id"]
    actions = list(candidate["actions"])
    taxonomy_descriptions = taxonomy_descriptions or {}
    for action in actions:
        description = taxonomy_descriptions.get(action.get("observed_label", ""))
        if description:
            action["taxonomy_description"] = description
    evidence: list[dict[str, Any]] = [
        {
            "evidence_id": "resolved_cases_snapshot",
            "source_id": cases_source_id,
            "source_type": "dataset",
            "locator": next(item["locator"] for item in sources if item["source_id"] == cases_source_id),
            "claim": f"The supplied snapshot contains {candidate['case_count']} resolved cases for {surface_id}.",
            "grade": "observed_trace",
            "observed_at": None,
            "notes": "Historical behavior only; not proof of correctness or action legality.",
        }
    ]
    for action in actions:
        if action.get("taxonomy_description") and taxonomy_source_id:
            evidence.append({
                "evidence_id": f"taxonomy_{action['action_id']}",
                "source_id": taxonomy_source_id,
                "source_type": "document",
                "locator": next(item["locator"] for item in sources if item["source_id"] == taxonomy_source_id),
                "claim": f"Taxonomy defines {action['observed_label']!r} as {action['taxonomy_description']!r}.",
                "grade": "documented",
                "observed_at": None,
                "notes": "Authoritative description enriches the candidate criterion; it does not promote maturity or legality.",
            })
        evidence.append(
            {
                "evidence_id": _case_evidence_id(surface_id, action["action_id"]),
                "source_id": cases_source_id,
                "source_type": "dataset",
                "locator": next(item["locator"] for item in sources if item["source_id"] == cases_source_id),
                "claim": f"{action['case_count']} resolved cases used action label {action['observed_label']!r}.",
                "grade": "inferred",
                "observed_at": None,
                "notes": "The label was observed, but the action contract and legality are inferred and unverified.",
            }
        )

    fallback = next((item for item in actions if item["action_id"] == "human_review"), None)
    if fallback is None:
        fallback = {"action_id": "human_review", "observed_label": "human review", "case_count": 0}
        actions.append(fallback)
        evidence.append(
            {
                "evidence_id": "safe_human_review_fallback",
                "source_id": cases_source_id,
                "source_type": "analysis",
                "locator": next(item["locator"] for item in sources if item["source_id"] == cases_source_id),
                "claim": "Discovery output needs a non-executing human-review fallback.",
                "grade": "hypothetical",
                "observed_at": None,
                "notes": "Safety scaffold only; the real review mechanism must be verified.",
            }
        )

    observed = [item for item in actions if item["case_count"] > 0]
    observed_refs = [_case_evidence_id(surface_id, item["action_id"]) for item in observed]
    judgment_ids = [f"case_supports_{item['action_id']}" for item in observed if item["action_id"] != "human_review"]
    action_registry = []
    transitions = []
    candidates = []
    choices = []
    for action in actions:
        action_id = action["action_id"]
        is_fallback = action_id == "human_review"
        evidence_ref = (
            _case_evidence_id(surface_id, action_id)
            if action["case_count"] > 0
            else "safe_human_review_fallback"
        )
        action_registry.append(
            {
                "action_id": action_id,
                "description": (
                    "Pause automation for domain review."
                    if is_fallback
                    else f"Candidate historical resolution {action['observed_label']!r}."
                ),
                "aliases": [action["observed_label"]],
                "choose_when": (
                    "The evidence is insufficient, ambiguous, or not approved for automation."
                    if is_fallback
                    else f"A domain owner confirms {action['observed_label']!r} is legal and appropriate."
                ),
                "do_not_choose_when": (
                    "A reviewed deterministic policy safely resolves the case."
                    if is_fallback
                    else "The action is not verified, legal, or explicitly approved."
                ),
                "preconditions": ["case_exists == true"] if is_fallback else ["case_exists == true", "human_approved == true"],
                "parameters": [{"name": "case_ref", "type": "string", "required": True}],
                "outputs": ["candidate_resolution"],
                "side_effects": ["none_discovery_only"],
                "risk": "low" if is_fallback else "high",
                "reversible": bool(is_fallback),
                "requires_confirmation": not is_fallback,
                "allowed_from_states": ["awaiting_decision"],
                "destination_states": ["awaiting_human_review" if is_fallback else "resolved_candidate"],
                "success_condition": "review_requested" if is_fallback else "human_confirmed_resolution_recorded",
                "evidence_refs": [evidence_ref],
                "examples": [],
                "counterexamples": [],
            }
        )
        transitions.append(
            {
                "transition_id": f"awaiting_decision_to_{action_id}",
                "source_state": "awaiting_decision",
                "action_id": action_id,
                "destination_state": "awaiting_human_review" if is_fallback else "resolved_candidate",
                "guard": ["case_exists == true"] if is_fallback else ["case_exists == true", "human_approved == true"],
                "outcome": "review_requested" if is_fallback else "candidate_resolution_recorded",
                "evidence_refs": [evidence_ref],
            }
        )
        taxonomy = action.get("taxonomy_description")
        criterion = (
            "Evidence is ambiguous or automation is not approved."
            if is_fallback
            else (f"Case evidence matches the authoritative taxonomy meaning: {taxonomy} Human approval is still required."
                  if taxonomy else f"Case evidence supports {action['observed_label']!r}; human approval is still required.")
        )
        refs = [evidence_ref] + ([f"taxonomy_{action_id}"] if taxonomy and taxonomy_source_id else [])
        candidates.append({"action_id": action_id, "criterion": criterion, "evidence_refs": refs})
        choices.append({"id": action_id, "criterion": criterion, "executor_action_id": action_id})

    judgments = []
    for action in observed:
        if action["action_id"] == "human_review":
            continue
        evidence_ref = _case_evidence_id(surface_id, action["action_id"])
        judgments.append(
            {
                "judgment_id": f"case_supports_{action['action_id']}",
                "family_id": "case_supports_action",
                "type": "noul",
                "instructions": f"Does `context.case` support the historical resolution {action['observed_label']!r}?",
                "criteria": {
                    "true": (f"Evidence matches the taxonomy definition: {taxonomy}." if taxonomy else f"Evidence supports the meaning of {action['observed_label']!r}."),
                    "false": "Evidence contradicts that resolution or is insufficient.",
                },
                "state_paths": ["context.case"],
                "activate_when": ["decision_status == 'pending'"],
                "surface_ids": [surface_id],
                "purpose": "Candidate semantic feature; it cannot authorize the action.",
                "maturity": "candidate",
                "evidence_refs": [evidence_ref] + ([f"taxonomy_{action['action_id']}"] if taxonomy and taxonomy_source_id else []),
            }
        )

    semantic_links = [
        {
            "link_id": "cases_support_case_context",
            "from": {"kind": "source", "id": cases_source_id},
            "to": {"kind": "concept", "id": "case_context"},
            "relation": "observes",
            "evidence_refs": ["resolved_cases_snapshot"],
        }
    ]
    for action in observed:
        evidence_ref = _case_evidence_id(surface_id, action["action_id"])
        if action["action_id"] != "human_review":
            judgment_id = f"case_supports_{action['action_id']}"
            semantic_links.extend(
                [
                    {
                        "link_id": f"resolution_support_to_{judgment_id}",
                        "from": {"kind": "concept", "id": "resolution_support"},
                        "to": {"kind": "judgment", "id": judgment_id},
                        "relation": "measured_by",
                        "evidence_refs": [evidence_ref],
                    },
                    {
                        "link_id": f"{judgment_id}_informs_surface",
                        "from": {"kind": "judgment", "id": judgment_id},
                        "to": {"kind": "surface", "id": surface_id},
                        "relation": "informs",
                        "evidence_refs": [evidence_ref],
                    },
                ]
            )
        semantic_links.append(
            {
                "link_id": f"surface_offers_{action['action_id']}",
                "from": {"kind": "surface", "id": surface_id},
                "to": {"kind": "action", "id": action["action_id"]},
                "relation": "offers_candidate",
                "evidence_refs": [evidence_ref],
            }
        )

    bundle = init_semantic_bundle.empty_bundle()
    bundle.update(
        {
            "material_manifest.yaml": {
                "schema_version": "2.0",
                "system_id": system_id,
                "scope": scope,
                "sources": [
                    {key: item[key] for key in ("source_id", "kind", "locator", "authority", "included")}
                    for item in sources
                ],
            },
            "semantic_ir.yaml": {
                "schema_version": "2.0",
                "system_id": system_id,
                "concepts": [
                    {
                        "concept_id": "case_context",
                        "kind": "fact",
                        "description": "The contextual material supplied for one unresolved decision instance.",
                        "observable": True,
                        "value_type": "object",
                        "evidence_refs": ["resolved_cases_snapshot"],
                    },
                    {
                        "concept_id": "resolution_support",
                        "kind": "semantic_property",
                        "description": "Whether case evidence supports one candidate historical resolution.",
                        "observable": False,
                        "value_type": "probability",
                        "evidence_refs": observed_refs,
                    },
                ],
                "relations": [
                    {
                        "relation_id": "case_has_resolution_support",
                        "subject_id": "case_context",
                        "predicate": "has_semantic_property",
                        "object_id": "resolution_support",
                        "evidence_refs": observed_refs,
                    }
                ],
            },
            "judgment_registry.yaml": {
                "schema_version": "2.0",
                "system_id": system_id,
                "families": [
                    {
                        "family_id": "case_supports_action",
                        "type": "noul",
                        "instructions_template": "Does {case} support the candidate resolution {action}?",
                        "parameters": ["case", "action"],
                        "purpose": "Discover reusable semantic boundaries before action selection.",
                        "maturity": "candidate",
                        "evidence_refs": ["resolved_cases_snapshot"],
                    }
                ],
                "judgments": judgments,
            },
            "semantic_links.yaml": {"schema_version": "2.0", "system_id": system_id, "links": semantic_links},
            "surface_candidates.yaml": {
                "schema_version": "1.0",
                "discovery_mode": "resolved_case_history",
                "source_root": next(item["locator"] for item in sources if item["source_id"] == cases_source_id),
                "review_required": True,
                "surface_candidates": [
                    {
                        "candidate_id": surface_id,
                        "input_description": "One unresolved case and its contextual material.",
                        "observed_options": [item["action_id"] for item in observed],
                        "bounded_output": True,
                        "semantic_interpretation_required": True,
                        "current_implementation": "historical_human_resolution",
                        "replacement_strength": "weak",
                        "source_locator": next(item["locator"] for item in sources if item["source_id"] == cases_source_id),
                        "review_status": "candidate",
                    }
                ],
            },
            "action_registry.yaml": {"schema_version": "1.0", "system_id": system_id, "actions": action_registry},
            "state_registry.yaml": {
                "schema_version": "1.0",
                "system_id": system_id,
                "states": [
                    {
                        "state_id": "awaiting_decision",
                        "description": "A case is waiting for a bounded decision.",
                        "observable_predicate": "decision_status == 'pending'",
                        "entry_evidence_refs": ["resolved_cases_snapshot"],
                        "terminal": False,
                    },
                    {
                        "state_id": "resolved_candidate",
                        "description": "A human-confirmed candidate resolution was recorded.",
                        "observable_predicate": "decision_status == 'resolved'",
                        "entry_evidence_refs": ["resolved_cases_snapshot"],
                        "terminal": True,
                    },
                    {
                        "state_id": "awaiting_human_review",
                        "description": "Automation stopped pending domain review.",
                        "observable_predicate": "decision_status == 'human_review'",
                        "entry_evidence_refs": [
                            _case_evidence_id(surface_id, "human_review")
                            if any(item["action_id"] == "human_review" and item["case_count"] > 0 for item in actions)
                            else "safe_human_review_fallback"
                        ],
                        "terminal": False,
                    },
                ],
            },
            "transition_graph.yaml": {"schema_version": "1.0", "system_id": system_id, "transitions": transitions},
            "decision_surfaces.yaml": {
                "schema_version": "1.0",
                "system_id": system_id,
                "decision_surfaces": [
                    {
                        "surface_id": surface_id,
                        "description": f"Candidate bounded decision discovered from resolved cases for {surface_id}.",
                        "activation": {"state_id": "awaiting_decision"},
                        "candidate_actions": candidates,
                        "fallback_action": "human_review",
                        "abstention_choice": "human_review",
                        "production": False,
                        "evidence_refs": ["resolved_cases_snapshot"],
                        "supporting_judgments": judgment_ids,
                    }
                ],
            },
            "evidence_ledger.jsonl": evidence,
            "jev_adapter_spec.yaml": {
                "schema_version": "1.0",
                "system_id": system_id,
                "provider": "vendor_neutral",
                "policy": {"min_confidence": 1.0},
                "classifier_questions": [
                    {
                        "question_id": f"choose_{surface_id}_action",
                        "surface_id": surface_id,
                        "type": "choice",
                        "instruction": "Choose the best-supported candidate; abstain to human review when uncertain.",
                        "choices": choices,
                        "abstention_choice": "human_review",
                    }
                ],
            },
            "coverage_report.md": (
                "# Candidate discovery coverage\n\n"
                f"Generated from {candidate['case_count']} resolved cases for `{surface_id}`.\n\n"
                "## Safety status\n\n"
                "- Non-production surface.\n"
                "- No executable bindings were inferred.\n"
                "- Historical actions require human approval.\n"
                "- Semantic judgments remain candidates until domain review and calibration.\n\n"
                "## Required before release\n\n"
                "Verify policy authority, legal preconditions, bindings, representative labels, and held-out JEV performance.\n"
            ),
        }
    )
    return bundle


def write_outputs(
    output: Path,
    *,
    system_id: str,
    scope: str,
    cases_path: Path,
    cases: list[Case],
    sources: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    splits: dict[str, tuple[list[Case], list[Case]]],
    case_fields: Mapping[str, str | None],
    test_fraction: float,
    confidence_threshold: float,
    review_minutes: float,
    hourly_cost: float | None,
    monthly_volume: int | None,
    force: bool,
    taxonomy_descriptions: Mapping[str, str] | None = None,
    taxonomy_source_id: str | None = None,
) -> Path | None:
    if output.exists() and not output.is_dir():
        raise DiscoveryError(f"output path exists and is not a directory: {output}")
    if output.exists() and any(output.iterdir()) and not force:
        raise DiscoveryError(f"output directory is not empty: {output}; use --force to overwrite generated files")
    output.mkdir(parents=True, exist_ok=True)
    total_cases = len(cases)
    for candidate in candidates:
        share = candidate["case_count"] / total_cases
        volume = round(monthly_volume * share) if monthly_volume is not None else None
        monthly_hours = round(volume * review_minutes / 60, 2) if volume is not None else None
        monthly_labor_value = round(monthly_hours * hourly_cost, 2) if monthly_hours is not None and hourly_cost is not None else None
        candidate["value_estimate"] = {
            "review_minutes_per_case": review_minutes,
            "observed_review_hours": round(candidate["case_count"] * review_minutes / 60, 2),
            "hourly_cost": hourly_cost,
            "observed_labor_value": round(candidate["case_count"] * review_minutes / 60 * hourly_cost, 2)
            if hourly_cost is not None
            else None,
            "estimated_monthly_volume": volume,
            "estimated_monthly_review_hours": monthly_hours,
            "estimated_monthly_labor_value": monthly_labor_value,
            "note": "Workload estimate only; not promised savings or release evidence.",
        }
        components = [{"name": "readiness", "score": candidate["readiness_score"], "weight": 0.7}]
        if monthly_hours is not None:
            components.append({"name": "monthly_review_hours", "score": min(monthly_hours / 100, 1) * 100, "weight": 0.2})
        if monthly_labor_value is not None:
            components.append({"name": "monthly_labor_value", "score": min(monthly_labor_value / 5000, 1) * 100, "weight": 0.1})
        weight = sum(item["weight"] for item in components)
        candidate["priority_score"] = round(sum(item["score"] * item["weight"] for item in components) / weight, 2)
        candidate["priority_components"] = components
    candidates.sort(key=lambda item: (-item["priority_score"], item["surface_id"]))

    manifest = {
        "schema_version": "1.0",
        "system_id": system_id,
        "scope": scope,
        "cases": {
            "locator": str(cases_path.resolve()),
            "sha256": _sha256(cases_path.resolve()),
            "case_count": total_cases,
            "surface_count": len(candidates),
            "fields": dict(case_fields),
            "test_fraction": test_fraction,
            "confidence_threshold": confidence_threshold,
        },
        "sources": sources,
        "privacy": "Raw case context is processed in memory and is not copied to discovery outputs.",
        "authority": "Historical labels are observations, not policy or legality evidence.",
    }
    (output / "discovery_manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (output / "decision_candidates.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": "1.0", "system_id": system_id, "review_required": True, "candidates": candidates},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with (output / "evaluation_cases.jsonl").open("w", encoding="utf-8") as handle:
        for surface_id, (train, test) in sorted(splits.items()):
            for split_name, split_cases in (("train", train), ("holdout", test)):
                for case in sorted(split_cases, key=lambda item: item.case_id):
                    handle.write(
                        json.dumps(
                            {
                                "case_ref": hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:16],
                                "surface_id": surface_id,
                                "split": split_name,
                                "expected_action_id": case.action_id,
                                "outcome_present": case.outcome_present,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

    eligible = [item for item in candidates if item["status"] == "candidate_for_human_review"]
    bundle_path: Path | None = None
    if eligible:
        top = eligible[0]
        cases_source_id = next(item["source_id"] for item in sources if Path(item["locator"]).resolve() == cases_path.resolve())
        bundle_path = output / "candidate_bundle"
        bundle = candidate_bundle(system_id, scope, top, sources, cases_source_id, taxonomy_descriptions, taxonomy_source_id)
        init_semantic_bundle.write_bundle(bundle_path, bundle, force=True)
        errors, warnings, counts = validate_semantic_bundle.validate(bundle_path)
        if errors:
            raise DiscoveryError("generated candidate bundle is invalid: " + "; ".join(errors))
        validation = {"valid": True, "warnings": warnings, "counts": counts}
        (output / "bundle_validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Decision discovery report",
        "",
        f"System: `{system_id}`  ",
        f"Resolved cases: {total_cases}  ",
        f"Candidate surfaces: {len(candidates)}",
        "",
        "Historical actions are observed behavior. They are not proof of policy correctness, legality, or executable bindings.",
        "",
        "## Ranked surfaces",
        "",
        "| Rank | Surface | Priority | Readiness | Status | Cases | Actions | Holdout accuracy | Confident coverage |",
        "| ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for rank, item in enumerate(candidates, 1):
        baseline_item = item["baseline"]
        accuracy = f"{baseline_item['accuracy']:.1%}" if baseline_item.get("accuracy") is not None else "n/a"
        coverage = f"{baseline_item['confident_coverage']:.1%}" if baseline_item.get("confident_coverage") is not None else "n/a"
        lines.append(
            f"| {rank} | `{item['surface_id']}` | {item['priority_score']:.2f} | {item['readiness_score']:.2f} | {item['status']} | "
            f"{item['case_count']} | {item['action_count']} | {accuracy} | {coverage} |"
        )
    lines.extend(
        [
            "",
            "The reported classifier is an offline discovery baseline, not JEV performance and not a release gate.",
            "",
            "## Generated system",
            "",
        ]
    )
    if bundle_path:
        lines.extend(
            [
                f"`{bundle_path.name}/` contains the highest-ranked candidate surface.",
                "",
                "- The surface is non-production.",
                "- Discovered actions have no executable bindings.",
                "- Non-fallback actions require explicit human approval.",
                "- Candidate semantic judgments cannot authorize an action.",
            ]
        )
    else:
        lines.append("No bundle was generated because every surface failed the minimum evidence gate.")
    lines.extend(
        [
            "",
            "## Next gate",
            "",
            "A domain owner must review the vocabulary, policy authority, legal preconditions, and labels before shadow-mode JEV evaluation.",
            "",
        ]
    )
    (output / "discovery_report.md").write_text("\n".join(lines), encoding="utf-8")
    return bundle_path


def discover(args: argparse.Namespace) -> dict[str, Any]:
    if not 0 < args.test_fraction < 0.5:
        raise DiscoveryError("--test-fraction must be greater than 0 and less than 0.5")
    if args.min_cases < 2 or args.min_action_cases < 1 or args.max_actions < 2:
        raise DiscoveryError("minimum cases/actions must be positive and --max-actions must be at least 2")
    if not 0 <= args.confidence_threshold <= 1:
        raise DiscoveryError("--confidence-threshold must be between 0 and 1")
    if (
        args.review_minutes < 0
        or (args.hourly_cost is not None and args.hourly_cost < 0)
        or (args.monthly_volume is not None and args.monthly_volume < 0)
    ):
        raise DiscoveryError("cost and time inputs cannot be negative")
    cases_path = args.cases.expanduser().resolve()
    cases = load_cases(
        cases_path,
        case_id_field=args.case_id_field,
        text_field=args.text_field,
        action_field=args.action_field,
        outcome_field=args.outcome_field,
        surface_field=args.surface_field,
    )
    sources = inventory_sources(args.source, cases_path)
    taxonomy_path_arg = getattr(args, "taxonomy", None)
    taxonomy_descriptions = load_taxonomy_descriptions(taxonomy_path_arg, {case.action_label for case in cases}) if taxonomy_path_arg else {}
    taxonomy_source_id = None
    if taxonomy_path_arg:
        taxonomy_path = taxonomy_path_arg.expanduser().resolve()
        taxonomy_source_id = next((item["source_id"] for item in sources if Path(item["locator"]).resolve() == taxonomy_path), None)
    grouped: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        grouped[case.surface].append(case)
    candidates: list[dict[str, Any]] = []
    splits: dict[str, tuple[list[Case], list[Case]]] = {}
    for surface_id, surface_cases in grouped.items():
        candidate, train, test = analyze_surface(
            surface_id,
            surface_cases,
            min_cases=args.min_cases,
            min_action_cases=args.min_action_cases,
            max_actions=args.max_actions,
            test_fraction=args.test_fraction,
            confidence_threshold=args.confidence_threshold,
        )
        candidates.append(candidate)
        splits[surface_id] = (train, test)
    candidates.sort(key=lambda item: (-item["readiness_score"], item["surface_id"]))
    system_id = slug(args.system_id, fallback="discovered_system")
    scope = args.scope or "Discover bounded decisions from supplied material and resolved cases."
    bundle_path = write_outputs(
        args.output.expanduser().resolve(),
        system_id=system_id,
        scope=scope,
        cases_path=cases_path,
        cases=cases,
        sources=sources,
        candidates=candidates,
        splits=splits,
        case_fields={
            "case_id": args.case_id_field,
            "text": args.text_field,
            "action": args.action_field,
            "outcome": args.outcome_field,
            "surface": args.surface_field,
        },
        test_fraction=args.test_fraction,
        confidence_threshold=args.confidence_threshold,
        review_minutes=args.review_minutes,
        hourly_cost=args.hourly_cost,
        monthly_volume=args.monthly_volume,
        force=args.force,
        taxonomy_descriptions=taxonomy_descriptions,
        taxonomy_source_id=taxonomy_source_id,
    )
    return {
        "output": str(args.output.expanduser().resolve()),
        "cases": len(cases),
        "sources": len(sources),
        "candidates": len(candidates),
        "eligible": sum(item["status"] == "candidate_for_human_review" for item in candidates),
        "candidate_bundle": str(bundle_path) if bundle_path else None,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--cases", type=Path, required=True, help="resolved cases as JSONL/NDJSON or CSV")
    result.add_argument("--source", type=Path, action="append", default=[], help="source file or directory; repeatable")
    result.add_argument("--taxonomy", type=Path, help="optional JSON/YAML label-to-description taxonomy")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--system-id", required=True)
    result.add_argument("--scope")
    result.add_argument("--case-id-field", default="case_id")
    result.add_argument("--text-field", default="message")
    result.add_argument("--action-field", default="resolved_action")
    result.add_argument("--outcome-field", default="outcome")
    result.add_argument("--surface-field")
    result.add_argument("--min-cases", type=int, default=20)
    result.add_argument("--min-action-cases", type=int, default=3)
    result.add_argument("--max-actions", type=int, default=12)
    result.add_argument("--test-fraction", type=float, default=0.2)
    result.add_argument("--confidence-threshold", type=float, default=0.8)
    result.add_argument("--review-minutes", type=float, default=2.0)
    result.add_argument("--hourly-cost", type=float)
    result.add_argument("--monthly-volume", type=int)
    result.add_argument("--force", action="store_true", help="overwrite known generated files; unknown files are preserved")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = discover(args)
    except DiscoveryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
