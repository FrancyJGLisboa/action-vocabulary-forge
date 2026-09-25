#!/usr/bin/env python3
"""Reproducible direct-acquisition benchmark over pinned public systems."""

from __future__ import annotations

import contextlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Iterator

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acquire_work_system_evidence as acquisition  # noqa: E402
import forge  # noqa: E402
import work_system_benchmark as public_benchmark  # noqa: E402


def _fixture_path(source: dict, fixture_root: Path) -> Path:
    return fixture_root / source["file"]


def _target_path(source: dict, material_root: Path) -> Path:
    kind = source["kind"]
    original = Path(source["file"])
    if kind == "deterministic_workflow":
        return material_root / ".github" / "workflows" / original.name
    if kind == "interface_template":
        return material_root / ".github" / "PULL_REQUEST_TEMPLATE" / original.name
    return material_root / original.name


def _prepare_material(system: dict, *, fixture_root: Path, material_root: Path) -> None:
    for source in system["sources"]:
        target = _target_path(source, material_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_fixture_path(source, fixture_root), target)


def _stable_contract(project: Path) -> dict:
    document = yaml.safe_load((project / "evidence_manifest.yaml").read_text(encoding="utf-8"))
    system = document["systems"][0]
    return {
        "system_id": system["system_id"],
        "repository": system["repository"],
        "commit": system["commit"],
        "sources": [
            {
                "source_id": item["source_id"],
                "kind": item["kind"],
                "evidence_status": item["evidence_status"],
                "sha256": item["sha256"],
                "origin": item["origin"],
            }
            for item in system["sources"]
        ],
    }


def evaluate() -> dict:
    public_manifest = public_benchmark.load_manifest()
    fixture_root = public_benchmark.FIXTURE_ROOT
    rows = []
    with tempfile.TemporaryDirectory(prefix="forge-direct-benchmark-") as directory:
        root = Path(directory)
        for system in public_manifest["systems"]:
            material = root / "material" / system["system_id"]
            _prepare_material(system, fixture_root=fixture_root, material_root=material)

            @contextlib.contextmanager
            def materializer(url: str) -> Iterator[acquisition.RepositorySnapshot]:
                yield acquisition.RepositorySnapshot(
                    root=material,
                    repository=url,
                    commit=system["commit"],
                )

            projects = [root / "runs" / f"{system['system_id']}-{index}" for index in (1, 2)]
            for project in projects:
                forge.acquire_reconstruction_project(
                    project=project,
                    system_id=system["system_id"],
                    repositories=[system["repository"]],
                    repository_materializer=materializer,
                )

            first = _stable_contract(projects[0])
            second = _stable_contract(projects[1])
            expected_hashes = {source["sha256"] for source in system["sources"]}
            acquired_hashes = {source["sha256"] for source in first["sources"]}
            kinds = {source["kind"] for source in first["sources"]}
            passed = (
                first == second
                and acquired_hashes == expected_hashes
                and len(first["sources"]) == len(system["sources"])
                and len(kinds) >= 2
                and all(
                    source["origin"]["locator"].startswith(system["repository"])
                    and f"/blob/{system['commit']}/" in source["origin"]["locator"]
                    for source in first["sources"]
                )
            )
            rows.append(
                {
                    "system_id": system["system_id"],
                    "repository": system["repository"],
                    "commit": system["commit"],
                    "sources": len(first["sources"]),
                    "source_kinds": len(kinds),
                    "deterministic": first == second,
                    "provenance_match": acquired_hashes == expected_hashes,
                    "status": "PASS" if passed else "FAIL",
                }
            )
    return {
        "systems": rows,
        "status": "PASS" if len(rows) >= 2 and all(row["status"] == "PASS" for row in rows) else "FAIL",
    }


def main() -> int:
    report = evaluate()
    for row in report["systems"]:
        print(
            f"{row['system_id']}: {row['sources']} sources, {row['source_kinds']} kinds, "
            f"deterministic={row['deterministic']}, provenance={row['provenance_match']} [{row['status']}]"
        )
    print(f"DIRECT ACQUISITION BENCHMARK: {report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
