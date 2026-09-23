#!/usr/bin/env python3
"""Validate a Semantic Decision Bundle and its embedded Action Bundle."""

from __future__ import annotations

import argparse
import json
import string
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from predicates import PredicateError, parse_predicate  # noqa: E402
import validate_action_bundle  # noqa: E402


REQUIRED_FILES = (
    "material_manifest.yaml",
    "semantic_ir.yaml",
    "judgment_registry.yaml",
    "semantic_links.yaml",
)
SOURCE_KINDS = {"code", "api", "ui", "sop", "document", "log", "trace", "dataset", "schema", "analysis", "other"}
AUTHORITIES = {"authoritative", "operational", "contextual"}
CONCEPT_KINDS = {"entity", "state_variable", "fact", "condition", "policy", "outcome", "semantic_property", "action_concept", "other"}
MATURITIES = {"candidate", "reviewed", "calibrated", "retired"}
QUESTION_TYPES = {"choice", "noul", "score"}
PRODUCTION_GRADES = {"verified_runtime", "verified_schema", "documented", "observed_trace"}
FORBIDDEN_JUDGMENT_FIELDS = {"authorizes_actions", "legal_actions", "legal_when", "preconditions"}


def load_yaml(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{path.name}: invalid YAML: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name}: top-level value must be a mapping")
        return {}
    return value


def mapping_items(doc: dict[str, Any], key: str, owner: str, errors: list[str]) -> list[dict[str, Any]]:
    value = doc.get(key, [])
    if not isinstance(value, list):
        errors.append(f"{owner}: {key} must be a list")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{owner}.{key}[{index}]: must be a mapping")
        else:
            result.append(item)
    return result


def unique(items: list[dict[str, Any]], field: str, owner: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        value = item.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{owner}[{index}]: missing {field}")
            continue
        if value in result:
            errors.append(f"{owner}: duplicate {field} {value!r}")
        result[value] = item
    return result


def load_evidence(bundle: Path, errors: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    path = bundle / "evidence_ledger.jsonl"
    if not path.is_file():
        return result
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"evidence_ledger.jsonl:{line_number}: invalid JSON: {exc}")
            continue
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str):
            result[item["evidence_id"]] = item
    return result


def validate(bundle: Path) -> tuple[list[str], list[str], dict[str, int]]:
    action_errors, action_warnings, action_counts = validate_action_bundle.validate(bundle)
    errors = list(action_errors)
    warnings = list(action_warnings)
    missing = [name for name in REQUIRED_FILES if not (bundle / name).is_file()]
    errors.extend(f"missing required semantic file: {name}" for name in missing)
    if missing:
        return errors, warnings, action_counts

    manifest = load_yaml(bundle / "material_manifest.yaml", errors)
    ir = load_yaml(bundle / "semantic_ir.yaml", errors)
    registry = load_yaml(bundle / "judgment_registry.yaml", errors)
    links_doc = load_yaml(bundle / "semantic_links.yaml", errors)
    actions_doc = load_yaml(bundle / "action_registry.yaml", errors)
    states_doc = load_yaml(bundle / "state_registry.yaml", errors)
    surfaces_doc = load_yaml(bundle / "decision_surfaces.yaml", errors)
    evidence = load_evidence(bundle, errors)

    semantic_docs = {
        "material_manifest.yaml": manifest,
        "semantic_ir.yaml": ir,
        "judgment_registry.yaml": registry,
        "semantic_links.yaml": links_doc,
    }
    for name, doc in semantic_docs.items():
        if str(doc.get("schema_version")) != "2.0":
            errors.append(f"{name}: schema_version must be '2.0'")
    system_docs = {
        **semantic_docs,
        "action_registry.yaml": actions_doc,
        "state_registry.yaml": states_doc,
        "decision_surfaces.yaml": surfaces_doc,
    }
    for name, doc in system_docs.items():
        if not isinstance(doc.get("system_id"), str) or not doc["system_id"]:
            errors.append(f"{name}: system_id must be a non-empty string")
    system_ids = {
        doc.get("system_id")
        for doc in system_docs.values()
        if isinstance(doc.get("system_id"), str) and doc["system_id"]
    }
    if len(system_ids) != 1:
        errors.append(f"semantic bundle: system_id values disagree: {sorted(system_ids)}")

    sources = unique(mapping_items(manifest, "sources", "material_manifest.yaml", errors), "source_id", "sources", errors)
    for source_id, source in sources.items():
        if source.get("kind") not in SOURCE_KINDS:
            errors.append(f"source {source_id}: invalid kind {source.get('kind')!r}")
        if source.get("authority") not in AUTHORITIES:
            errors.append(f"source {source_id}: invalid authority {source.get('authority')!r}")
        if not isinstance(source.get("locator"), str) or not source.get("locator"):
            errors.append(f"source {source_id}: locator must be a non-empty string")
        if not isinstance(source.get("included"), bool):
            errors.append(f"source {source_id}: included must be boolean")
    for evidence_id, item in evidence.items():
        if item.get("source_id") not in sources:
            errors.append(f"evidence {evidence_id}: source_id {item.get('source_id')!r} is absent from material_manifest.yaml")

    def check_evidence(values: Any, owner: str, production: bool = False) -> None:
        if not isinstance(values, list):
            errors.append(f"{owner}: evidence_refs must be a list")
            return
        for ref in values:
            if ref not in evidence:
                errors.append(f"{owner}: unknown evidence reference {ref!r}")
        if production and not any(evidence.get(ref, {}).get("grade") in PRODUCTION_GRADES for ref in values):
            errors.append(f"{owner}: reviewed judgment has no production-grade evidence")

    concepts = unique(mapping_items(ir, "concepts", "semantic_ir.yaml", errors), "concept_id", "concepts", errors)
    for concept_id, concept in concepts.items():
        if concept.get("kind") not in CONCEPT_KINDS:
            errors.append(f"concept {concept_id}: invalid kind {concept.get('kind')!r}")
        if not concept.get("description"):
            errors.append(f"concept {concept_id}: missing description")
        if not isinstance(concept.get("observable"), bool):
            errors.append(f"concept {concept_id}: observable must be boolean")
        check_evidence(concept.get("evidence_refs"), f"concept {concept_id}")

    relations = unique(mapping_items(ir, "relations", "semantic_ir.yaml", errors), "relation_id", "relations", errors)
    for relation_id, relation in relations.items():
        for field in ("subject_id", "object_id"):
            if relation.get(field) not in concepts:
                errors.append(f"relation {relation_id}: unknown {field} {relation.get(field)!r}")
        if not relation.get("predicate"):
            errors.append(f"relation {relation_id}: missing predicate")
        check_evidence(relation.get("evidence_refs"), f"relation {relation_id}")

    families = unique(mapping_items(registry, "families", "judgment_registry.yaml", errors), "family_id", "families", errors)
    formatter = string.Formatter()
    for family_id, family in families.items():
        kind = family.get("type")
        if kind not in QUESTION_TYPES:
            errors.append(f"family {family_id}: invalid type {kind!r}")
        parameters = family.get("parameters")
        if not isinstance(parameters, list) or not all(isinstance(value, str) and value for value in parameters):
            errors.append(f"family {family_id}: parameters must be a list of names")
            parameters = []
        template = family.get("instructions_template")
        if not isinstance(template, str) or not template:
            errors.append(f"family {family_id}: missing instructions_template")
        else:
            fields = {name for _, name, _, _ in formatter.parse(template) if name}
            undeclared = fields - set(parameters)
            if undeclared:
                errors.append(f"family {family_id}: template uses undeclared parameters {sorted(undeclared)}")
        if family.get("maturity") not in MATURITIES:
            errors.append(f"family {family_id}: invalid maturity {family.get('maturity')!r}")
        check_evidence(family.get("evidence_refs"), f"family {family_id}")

    judgments = unique(mapping_items(registry, "judgments", "judgment_registry.yaml", errors), "judgment_id", "judgments", errors)
    surfaces = unique(mapping_items(surfaces_doc, "decision_surfaces", "decision_surfaces.yaml", errors), "surface_id", "surfaces", errors)
    actions = unique(mapping_items(actions_doc, "actions", "action_registry.yaml", errors), "action_id", "actions", errors)
    states = unique(mapping_items(states_doc, "states", "state_registry.yaml", errors), "state_id", "states", errors)
    for judgment_id, judgment in judgments.items():
        forbidden = sorted(FORBIDDEN_JUDGMENT_FIELDS & set(judgment))
        if forbidden:
            errors.append(f"judgment {judgment_id}: semantic judgments must not authorize actions ({', '.join(forbidden)})")
        kind = judgment.get("type")
        if kind not in QUESTION_TYPES:
            errors.append(f"judgment {judgment_id}: invalid type {kind!r}")
        if not isinstance(judgment.get("instructions"), (str, dict, list)):
            errors.append(f"judgment {judgment_id}: instructions must be text or structured JSON")
        criteria = judgment.get("criteria")
        if kind == "choice" and (not isinstance(criteria, dict) or len(criteria) < 2):
            errors.append(f"judgment {judgment_id}: Choice criteria must contain at least two options")
        if kind == "score" and (not isinstance(criteria, list) or len(criteria) < 2):
            errors.append(f"judgment {judgment_id}: Score criteria must contain at least two levels")
        if kind == "noul" and criteria is not None and not isinstance(criteria, dict):
            errors.append(f"judgment {judgment_id}: Noul criteria must be a mapping when present")
        state_paths = judgment.get("state_paths")
        if not isinstance(state_paths, list) or not state_paths or not all(isinstance(path, str) for path in state_paths):
            errors.append(f"judgment {judgment_id}: state_paths must be a non-empty list")
        predicates = judgment.get("activate_when", [])
        if not isinstance(predicates, list):
            errors.append(f"judgment {judgment_id}: activate_when must be a list")
        else:
            for predicate in predicates:
                try:
                    parse_predicate(predicate)
                except (PredicateError, TypeError) as exc:
                    errors.append(f"judgment {judgment_id}: invalid activation predicate {predicate!r} ({exc})")
        surface_ids = judgment.get("surface_ids", [])
        if not isinstance(surface_ids, list):
            errors.append(f"judgment {judgment_id}: surface_ids must be a list")
        else:
            for surface_id in surface_ids:
                if surface_id not in surfaces:
                    errors.append(f"judgment {judgment_id}: unknown surface_id {surface_id!r}")
        family_id = judgment.get("family_id")
        if family_id is not None and family_id not in families:
            errors.append(f"judgment {judgment_id}: unknown family_id {family_id!r}")
        maturity = judgment.get("maturity")
        if maturity not in MATURITIES:
            errors.append(f"judgment {judgment_id}: invalid maturity {maturity!r}")
        check_evidence(judgment.get("evidence_refs"), f"judgment {judgment_id}", maturity in {"reviewed", "calibrated"})
        if maturity == "calibrated" and not isinstance(judgment.get("evaluation"), dict):
            errors.append(f"judgment {judgment_id}: calibrated maturity requires evaluation metadata")

    linked_surfaces: set[str] = set()
    for surface_id, surface in surfaces.items():
        supporting = surface.get("supporting_judgments", [])
        if supporting is None:
            supporting = []
        if not isinstance(supporting, list):
            errors.append(f"surface {surface_id}: supporting_judgments must be a list")
            continue
        for judgment_id in supporting:
            if judgment_id not in judgments:
                errors.append(f"surface {surface_id}: unknown supporting judgment {judgment_id!r}")
            else:
                linked_surfaces.add(surface_id)
                if surface_id not in judgments[judgment_id].get("surface_ids", []):
                    errors.append(f"surface {surface_id}: judgment {judgment_id!r} does not link back through surface_ids")

    known: dict[str, set[str]] = {
        "source": set(sources),
        "concept": set(concepts),
        "family": set(families),
        "judgment": set(judgments),
        "state": set(states),
        "surface": set(surfaces),
        "action": set(actions),
    }
    links = unique(mapping_items(links_doc, "links", "semantic_links.yaml", errors), "link_id", "links", errors)
    for link_id, link in links.items():
        for endpoint in ("from", "to"):
            value = link.get(endpoint)
            if not isinstance(value, dict):
                errors.append(f"link {link_id}: {endpoint} must be a mapping")
                continue
            kind, identifier = value.get("kind"), value.get("id")
            if kind not in known:
                errors.append(f"link {link_id}: unknown {endpoint}.kind {kind!r}")
            elif identifier not in known[kind]:
                errors.append(f"link {link_id}: unknown {endpoint} {kind}:{identifier}")
        if not link.get("relation"):
            errors.append(f"link {link_id}: missing relation")
        check_evidence(link.get("evidence_refs"), f"link {link_id}")

    counts = {
        **action_counts,
        "sources": len(sources),
        "concepts": len(concepts),
        "relations": len(relations),
        "families": len(families),
        "judgments": len(judgments),
        "semantic_links": len(links),
        "linked_surfaces": len(linked_surfaces),
    }
    if judgments and not linked_surfaces:
        warnings.append("semantic bundle: judgments exist but no decision surface declares supporting_judgments")
    return errors, warnings, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    errors, warnings, counts = validate(args.bundle)
    validate_action_bundle.print_report(errors, warnings, counts)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
