#!/usr/bin/env python3
"""Compile heterogeneous evidence into a descriptive Work System Map.

System 2 may propose semantic nodes and links, but this compiler accepts them
only when every claim quotes a hashed source. Declared, observed, inferred,
unknown, and conflicting evidence remain distinct. The result never executes
or authorizes an action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import yaml


EVIDENCE_STATUSES = {"declared", "observed", "inferred", "unknown", "conflict"}
NODE_KINDS = {"actor", "artifact", "activity", "state", "decision", "outcome"}
SOURCE_STATUSES = {"declared", "observed"}


class ReconstructionError(ValueError):
    """Evidence cannot support the proposed work-system structure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReconstructionError(f"cannot read {path}: {exc}") from exc
    return digest.hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReconstructionError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReconstructionError(f"{path} must contain a mapping")
    return value


def _artifact_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ReconstructionError("artifact path must be a non-empty string")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ReconstructionError(f"artifact escapes source root: {relative}") from exc
    return path


def validate_system_spec(system: Any, *, root: Path) -> dict[str, Any]:
    if not isinstance(system, dict):
        raise ReconstructionError("system specification must be a mapping")
    required = {"system_id", "repository", "commit", "license", "proposal_file", "proposal_sha256", "sources"}
    if not required <= set(system):
        raise ReconstructionError("system specification lacks provenance fields")
    system_id = system["system_id"]
    if not isinstance(system_id, str) or not system_id:
        raise ReconstructionError("system_id must be a non-empty string")
    acquired = system.get("provenance_mode") == "acquired"
    if not acquired:
        if not str(system["repository"]).startswith("https://github.com/"):
            raise ReconstructionError(f"{system_id}: repository must be a GitHub URL")
        commit = str(system["commit"])
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise ReconstructionError(f"{system_id}: commit must be a full lowercase SHA")

    sources = system["sources"]
    minimum_sources = 1 if acquired else 2
    if not isinstance(sources, list) or len(sources) < minimum_sources:
        raise ReconstructionError(f"{system_id}: at least {minimum_sources} source(s) are required")
    seen: set[str] = set()
    kinds: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ReconstructionError(f"{system_id}: source entries must be mappings")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id or source_id in seen:
            raise ReconstructionError(f"{system_id}: invalid or duplicate source_id {source_id!r}")
        seen.add(source_id)
        kind = source.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ReconstructionError(f"{system_id}:{source_id}: source kind is required")
        kinds.add(kind)
        if source.get("evidence_status") not in SOURCE_STATUSES:
            raise ReconstructionError(f"{system_id}:{source_id}: source must be declared or observed")
        path = _artifact_path(root, source.get("file"))
        if not path.is_file() or sha256_file(path) != source.get("sha256"):
            raise ReconstructionError(f"{system_id}:{source_id}: source hash mismatch")
        if acquired:
            origin = source.get("origin")
            if not isinstance(origin, dict) or origin.get("type") not in {"local", "url", "repository"}:
                raise ReconstructionError(f"{system_id}:{source_id}: acquired source origin is required")
            locator = origin.get("locator")
            if not isinstance(locator, str) or not locator:
                raise ReconstructionError(f"{system_id}:{source_id}: acquired source locator is required")
            if origin["type"] == "url" and not locator.startswith("https://"):
                raise ReconstructionError(f"{system_id}:{source_id}: acquired URL must use HTTPS")
            if origin["type"] == "repository":
                repository = origin.get("repository")
                source_commit = origin.get("commit")
                if not isinstance(repository, str) or not repository.startswith("https://github.com/"):
                    raise ReconstructionError(f"{system_id}:{source_id}: repository origin must be GitHub HTTPS")
                if not isinstance(source_commit, str) or len(source_commit) != 40 or any(
                    character not in "0123456789abcdef" for character in source_commit
                ):
                    raise ReconstructionError(f"{system_id}:{source_id}: repository origin needs a full commit")
                if not locator.startswith(repository) or f"/blob/{source_commit}/" not in locator:
                    raise ReconstructionError(f"{system_id}:{source_id}: repository locator must be commit-pinned")
        else:
            urls = [source.get("url")] if source.get("url") else source.get("urls")
            if not isinstance(urls, list) or not urls or not all(
                isinstance(url, str) and url.startswith(system["repository"]) for url in urls
            ):
                raise ReconstructionError(f"{system_id}:{source_id}: public source URLs are required")
            if source["evidence_status"] == "declared" and not all(f"/blob/{commit}/" in url for url in urls):
                raise ReconstructionError(f"{system_id}:{source_id}: declared sources must be commit-pinned")
        if source["evidence_status"] == "observed" and not source.get("captured_at"):
            raise ReconstructionError(f"{system_id}:{source_id}: observed history needs captured_at")
    if len(kinds) < 2:
        raise ReconstructionError(f"{system_id}: heterogeneous source kinds are required")

    proposal_path = _artifact_path(root, system["proposal_file"])
    if not proposal_path.is_file() or sha256_file(proposal_path) != system["proposal_sha256"]:
        raise ReconstructionError(f"{system_id}: proposal hash mismatch")
    return system


def _source_texts(system: Mapping[str, Any], root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    sources = {source["source_id"]: dict(source) for source in system["sources"]}
    texts: dict[str, str] = {}
    for source_id, source in sources.items():
        path = _artifact_path(root, source["file"])
        try:
            texts[source_id] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReconstructionError(f"cannot read text source {source_id}: {exc}") from exc
    return sources, texts


def _validate_quote(
    evidence: Any,
    *,
    sources: Mapping[str, Mapping[str, Any]],
    texts: Mapping[str, str],
    context: str,
) -> str:
    if not isinstance(evidence, Mapping):
        raise ReconstructionError(f"{context}: evidence entries must be mappings")
    source_id = evidence.get("source_id")
    quote = evidence.get("quote")
    if source_id not in sources:
        raise ReconstructionError(f"{context}: unknown source reference {source_id!r}")
    if not isinstance(quote, str) or not quote.strip() or quote not in texts[source_id]:
        raise ReconstructionError(f"{context}: quote is not present in source {source_id!r}")
    return str(source_id)


def _validate_status(status: Any, *, context: str) -> str:
    if status not in EVIDENCE_STATUSES:
        raise ReconstructionError(f"{context}: invalid evidence status {status!r}")
    return str(status)


def reconstruct(
    system: Mapping[str, Any],
    *,
    root: Path,
    proposal_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    system = validate_system_spec(dict(system), root=root)
    sources, texts = _source_texts(system, root)
    proposal = (
        dict(proposal_override)
        if proposal_override is not None
        else load_yaml(_artifact_path(root, system["proposal_file"]))
    )
    if proposal.get("schema_version") != "1.0" or proposal.get("system_id") != system["system_id"]:
        raise ReconstructionError(f"{system['system_id']}: proposal identity does not match")

    raw_nodes = proposal.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ReconstructionError("proposal nodes must be a non-empty list")
    node_ids: set[str] = set()
    nodes: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ReconstructionError("nodes must be mappings")
        node_id = raw.get("node_id")
        kind = raw.get("kind")
        if not isinstance(node_id, str) or not node_id or node_id in node_ids:
            raise ReconstructionError(f"invalid or duplicate node_id {node_id!r}")
        if kind not in NODE_KINDS:
            raise ReconstructionError(f"node {node_id}: invalid kind {kind!r}")
        node_ids.add(node_id)
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ReconstructionError(f"node {node_id}: evidence is required")
        statuses: set[str] = set()
        source_ids: set[str] = set()
        for item in evidence:
            source_id = _validate_quote(item, sources=sources, texts=texts, context=f"node {node_id}")
            status = _validate_status(item.get("status"), context=f"node {node_id}")
            source_status = sources[source_id]["evidence_status"]
            if status == "observed" and source_status != "observed":
                raise ReconstructionError(f"node {node_id}: declared source cannot prove observed behavior")
            if status == "declared" and source_status != "declared":
                raise ReconstructionError(f"node {node_id}: observed history cannot establish declared policy")
            statuses.add(status)
            source_ids.add(source_id)
            status_counts[status] += 1
        candidate_actions = raw.get("candidate_actions")
        if kind == "decision":
            if (
                not isinstance(candidate_actions, list)
                or not 2 <= len(candidate_actions) <= 12
                or len(set(candidate_actions)) != len(candidate_actions)
                or not all(isinstance(action, str) and action for action in candidate_actions)
            ):
                raise ReconstructionError(f"decision {node_id}: bounded candidate_actions are required")
        elif candidate_actions is not None:
            raise ReconstructionError(f"node {node_id}: only decisions may declare candidate_actions")
        nodes.append(
            {
                "node_id": node_id,
                "kind": kind,
                "label": raw.get("label") or node_id,
                "candidate_actions": candidate_actions if kind == "decision" else None,
                "evidence_statuses": sorted(statuses),
                "source_ids": sorted(source_ids),
                "evidence": evidence,
            }
        )

    links: list[dict[str, Any]] = []
    for index, raw in enumerate(proposal.get("links") or [], 1):
        if not isinstance(raw, dict) or raw.get("from") not in node_ids or raw.get("to") not in node_ids:
            raise ReconstructionError(f"link {index}: endpoints must reference known nodes")
        status = _validate_status(raw.get("status"), context=f"link {index}")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ReconstructionError(f"link {index}: evidence is required")
        link_sources = {
            _validate_quote(item, sources=sources, texts=texts, context=f"link {index}")
            for item in evidence
        }
        if status == "observed" and any(sources[source_id]["evidence_status"] != "observed" for source_id in link_sources):
            raise ReconstructionError(f"link {index}: observed relationship requires observed evidence")
        links.append(
            {
                "from": raw["from"],
                "relation": raw.get("relation") or "related_to",
                "to": raw["to"],
                "status": status,
                "source_ids": sorted(link_sources),
                "evidence": evidence,
            }
        )

    by_id = {node["node_id"]: node for node in nodes}
    workflows: list[dict[str, Any]] = []
    for raw in proposal.get("workflows") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("workflow_id"), str):
            raise ReconstructionError("workflows require workflow_id")
        steps = raw.get("steps")
        actor_ids = raw.get("actor_ids")
        artifact_ids = raw.get("artifact_ids")
        decision_ids = raw.get("decision_ids")
        source_ids = raw.get("source_ids")
        gaps = raw.get("gaps")
        for field, values in (("steps", steps), ("actor_ids", actor_ids), ("artifact_ids", artifact_ids), ("decision_ids", decision_ids)):
            if not isinstance(values, list) or not values or any(value not in node_ids for value in values):
                raise ReconstructionError(f"workflow {raw['workflow_id']}: {field} must reference known nodes")
        if any(by_id[value]["kind"] != "actor" for value in actor_ids):
            raise ReconstructionError(f"workflow {raw['workflow_id']}: actor_ids contain non-actors")
        if any(by_id[value]["kind"] != "artifact" for value in artifact_ids):
            raise ReconstructionError(f"workflow {raw['workflow_id']}: artifact_ids contain non-artifacts")
        if any(by_id[value]["kind"] != "decision" for value in decision_ids):
            raise ReconstructionError(f"workflow {raw['workflow_id']}: decision_ids contain non-decisions")
        minimum_workflow_sources = 1 if system.get("provenance_mode") == "acquired" else 2
        if (
            not isinstance(source_ids, list)
            or len(set(source_ids)) < minimum_workflow_sources
            or any(value not in sources for value in source_ids)
        ):
            requirement = "source evidence" if minimum_workflow_sources == 1 else "cross-source evidence"
            raise ReconstructionError(f"workflow {raw['workflow_id']}: {requirement} is required")
        if minimum_workflow_sources == 2 and len({sources[value]["kind"] for value in source_ids}) < 2:
            raise ReconstructionError(f"workflow {raw['workflow_id']}: evidence must cross source forms")
        if not isinstance(gaps, list) or not gaps or not all(isinstance(gap, str) and gap.strip() for gap in gaps):
            raise ReconstructionError(f"workflow {raw['workflow_id']}: explicit evidence gaps are required")
        repeated_cases = raw.get("repeated_case_count")
        if not isinstance(repeated_cases, int) or repeated_cases < 0:
            raise ReconstructionError(f"workflow {raw['workflow_id']}: repeated_case_count must be non-negative")
        workflows.append(
            {
                "workflow_id": raw["workflow_id"],
                "label": raw.get("label") or raw["workflow_id"],
                "steps": steps,
                "actors": actor_ids,
                "artifacts": artifact_ids,
                "decisions": decision_ids,
                "source_ids": source_ids,
                "source_kinds": sorted({sources[value]["kind"] for value in source_ids}),
                "evidence_breadth": "cross_source" if len(set(source_ids)) >= 2 else "single_source",
                "repeated_case_count": repeated_cases,
                "recurrence_status": "observed_repeated" if repeated_cases >= 2 else "not_yet_repeated",
                "gaps": gaps,
            }
        )

    decisions = [node for node in nodes if node["kind"] == "decision"]
    return {
        "schema_version": "1.0",
        "system_id": system["system_id"],
        "mode": "work_system_reconstruction",
        "repository": {"url": system["repository"], "commit": system["commit"], "license": system["license"]},
        "source_inventory": [
            {
                "source_id": source["source_id"],
                "kind": source["kind"],
                "evidence_status": source["evidence_status"],
                "sha256": source["sha256"],
                "origin": source.get("origin"),
            }
            for source in system["sources"]
        ],
        "nodes": nodes,
        "links": links,
        "workflows": workflows,
        "decision_candidates": [
            {
                "node_id": node["node_id"],
                "candidate_actions": node["candidate_actions"],
                "maturity": "observed_candidate" if "observed" in node["evidence_statuses"] else "declared_candidate",
                "shadow_eligible": False,
            }
            for node in decisions
        ],
        "evidence_summary": dict(sorted(status_counts.items())),
        "gaps": [gap for workflow in workflows for gap in workflow["gaps"]],
        "safety": {
            "descriptive_only": True,
            "system2_proposals_validated": True,
            "inference_is_authority": False,
            "executes_actions": False,
            "production_authority": False,
            "shadow_eligible": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = load_yaml(args.manifest)
        systems = manifest.get("systems") or []
        system = next((item for item in systems if isinstance(item, dict) and item.get("system_id") == args.system_id), None)
        if system is None:
            raise ReconstructionError(f"system {args.system_id!r} is not present in the manifest")
        result = reconstruct(system, root=args.manifest.parent)
        rendered = yaml.safe_dump(result, sort_keys=False, allow_unicode=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except ReconstructionError as exc:
        print(f"WORK SYSTEM RECONSTRUCTION: FAIL - {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
