#!/usr/bin/env python3
"""Run the frozen public Work System Reconstruction V1 benchmark."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "work-system-benchmark"
sys.path.insert(0, str(ROOT / "scripts"))
import reconstruct_work_system as reconstruction  # noqa: E402


class WorkSystemBenchmarkError(ValueError):
    """The benchmark corpus or expected reconstruction is invalid."""


def load_manifest(path: Path = FIXTURE_ROOT / "manifest.yaml") -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise WorkSystemBenchmarkError(f"cannot read manifest: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise WorkSystemBenchmarkError("manifest schema_version must be '1.0'")
    systems = value.get("systems")
    if not isinstance(systems, list) or not systems:
        raise WorkSystemBenchmarkError("manifest systems must be a non-empty list")
    seen: set[str] = set()
    for system in systems:
        reconstruction.validate_system_spec(system, root=path.parent)
        system_id = system["system_id"]
        if system_id in seen:
            raise WorkSystemBenchmarkError(f"duplicate system_id: {system_id}")
        seen.add(system_id)
        expected = system.get("expected")
        if not isinstance(expected, dict) or not all(
            isinstance(expected.get(field), int) and expected[field] > 0
            for field in (
                "minimum_nodes",
                "minimum_decisions",
                "minimum_source_kinds_per_workflow",
                "repeated_workflows",
            )
        ):
            raise WorkSystemBenchmarkError(f"{system_id}: positive expected thresholds are required")
    return value


def coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    systems = manifest["systems"]
    repositories = {system["repository"] for system in systems}
    passing = []
    for system in systems:
        source_kinds = {source["kind"] for source in system["sources"]}
        has_history = any(source["evidence_status"] == "observed" for source in system["sources"])
        passing.append(len(source_kinds) >= 3 and has_history)
    status = "PASS" if len(systems) >= 2 and len(repositories) >= 2 and all(passing) else "HOLD"
    return {"status": status, "systems": len(systems), "repositories": len(repositories)}


def evaluate(manifest: Mapping[str, Any], *, root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for system in manifest["systems"]:
        result = reconstruction.reconstruct(system, root=root)
        expected = system["expected"]
        decisions = result["decision_candidates"]
        repeated = [workflow for workflow in result["workflows"] if workflow["recurrence_status"] == "observed_repeated"]
        checks = {
            "nodes": len(result["nodes"]) >= expected["minimum_nodes"],
            "decisions": len(decisions) >= expected["minimum_decisions"],
            "node_kinds": {node["kind"] for node in result["nodes"]} >= {
                "actor", "artifact", "activity", "state", "decision", "outcome"
            },
            "repeated_workflows": len(repeated) >= expected["repeated_workflows"],
            "source_forms": all(
                len(workflow["source_kinds"]) >= expected["minimum_source_kinds_per_workflow"]
                for workflow in result["workflows"]
            ),
            "gaps": all(workflow["gaps"] for workflow in result["workflows"]),
            "descriptive_only": result["safety"]["descriptive_only"] is True,
            "no_execution": result["safety"]["executes_actions"] is False,
            "no_shadow_promotion": all(not candidate["shadow_eligible"] for candidate in decisions),
        }
        if not all(checks.values()):
            failures.append(system["system_id"])
        rows.append(
            {
                "system_id": system["system_id"],
                "sources": len(result["source_inventory"]),
                "nodes": len(result["nodes"]),
                "links": len(result["links"]),
                "decisions": len(decisions),
                "workflows": len(result["workflows"]),
                "repeated_workflows": len(repeated),
                "gaps": len(result["gaps"]),
                "checks": checks,
            }
        )
    corpus_coverage = coverage(manifest)
    passed = corpus_coverage["status"] == "PASS" and not failures
    return {
        "status": "PASS" if passed else "HOLD",
        "coverage": corpus_coverage,
        "systems": rows,
        "failures": failures,
    }


def verify_contract(report: Mapping[str, Any]) -> bool:
    return report["status"] == "PASS" and all(row["checks"]["node_kinds"] for row in report["systems"])


def verify_cross_source(report: Mapping[str, Any]) -> bool:
    return report["status"] == "PASS" and all(
        row["checks"]["source_forms"]
        and row["checks"]["repeated_workflows"]
        and row["checks"]["gaps"]
        for row in report["systems"]
    )


def verify_network(manifest: Mapping[str, Any], *, root: Path = FIXTURE_ROOT) -> list[str]:
    """Re-extract declared excerpts from commit-pinned raw GitHub files."""
    failures: list[str] = []
    for system in manifest["systems"]:
        for source in system["sources"]:
            if source["evidence_status"] != "declared":
                continue
            extraction = source.get("extraction") or {}
            ranges = extraction.get("ranges")
            if extraction.get("mode") != "exact_line_segments" or not isinstance(ranges, list):
                failures.append(f"{system['system_id']}:{source['source_id']}: missing exact extraction")
                continue
            url = source["url"]
            raw_url = url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
            try:
                request = urllib.request.Request(raw_url, headers={"User-Agent": "decision-system-forge-benchmark"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    upstream = response.read().decode("utf-8").splitlines()
                selected: list[str] = []
                for span in ranges:
                    if (
                        not isinstance(span, list)
                        or len(span) != 2
                        or not all(isinstance(value, int) for value in span)
                        or span[0] < 1
                        or span[0] > span[1]
                        or span[1] > len(upstream)
                    ):
                        raise ValueError(f"invalid line range {span!r}")
                    selected.extend(upstream[span[0] - 1 : span[1]])
                extracted = ("\n".join(selected) + "\n").encode("utf-8")
                local = (root / source["file"]).read_bytes()
                if extracted != local:
                    failures.append(f"{system['system_id']}:{source['source_id']}: excerpt mismatch")
            except Exception as exc:  # pragma: no cover - network-only path
                failures.append(f"{system['system_id']}:{source['source_id']}: {exc}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list-systems", action="store_true")
    mode.add_argument("--verify-contract", action="store_true")
    mode.add_argument("--verify-cross-source", action="store_true")
    mode.add_argument("--verify-network", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        corpus_coverage = coverage(manifest)
        if args.list_systems:
            for system in manifest["systems"]:
                kinds = sorted({source["kind"] for source in system["sources"]})
                print(f"{system['system_id']}: {system['repository']}@{system['commit']} sources={','.join(kinds)}")
            print(f"PUBLIC SYSTEM COVERAGE: {corpus_coverage['status']}")
            return 0 if corpus_coverage["status"] == "PASS" else 2
        if args.verify_network:
            failures = verify_network(manifest)
            print("PUBLIC SOURCE PROVENANCE: " + ("PASS" if not failures else "FAIL - " + "; ".join(failures)))
            return 0 if not failures else 2
        report = evaluate(manifest)
        if args.verify_contract:
            status = "PASS" if verify_contract(report) else "HOLD"
            print(f"EVIDENCE CONTRACT: {status}")
            return 0 if status == "PASS" else 2
        if args.verify_cross_source:
            status = "PASS" if verify_cross_source(report) else "HOLD"
            print(f"CROSS-SOURCE WORKFLOWS: {status}")
            return 0 if status == "PASS" else 2
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            for row in report["systems"]:
                print(
                    f"{row['system_id']}: sources={row['sources']} nodes={row['nodes']} "
                    f"links={row['links']} decisions={row['decisions']} "
                    f"repeated_workflows={row['repeated_workflows']} gaps={row['gaps']}"
                )
            print(f"WORK SYSTEM RECONSTRUCTION: {report['status']}")
        return 0 if report["status"] == "PASS" else 2
    except (WorkSystemBenchmarkError, reconstruction.ReconstructionError) as exc:
        print(f"WORK SYSTEM RECONSTRUCTION: FAIL - {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
