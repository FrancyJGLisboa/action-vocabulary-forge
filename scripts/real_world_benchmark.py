#!/usr/bin/env python3
"""Run the frozen, provenance-backed Real-world Benchmark V1."""

from __future__ import annotations

import argparse
import json
import hashlib
import urllib.request
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "real-world-benchmark"
sys.path.insert(0, str(ROOT / "scripts"))
import scan_decision_opportunities as scanner  # noqa: E402

SHAPES = {"bounded_semantic_decision", "deterministic_rule", "open_generation", "not_a_decision"}
REQUIRED = {"entry_id", "archetype", "source_file", "source_url", "commit", "license", "source_locator", "raw_url", "provenance_status", "source_sha256", "reviewed_excerpt", "expected_shape", "reviewer_rationale"}


class BenchmarkError(ValueError):
    pass


def load_manifest(path: Path = FIXTURE_ROOT / "manifest.yaml") -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkError(f"cannot read manifest: {exc}") from exc
    return validate_manifest(data)


def validate_manifest(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise BenchmarkError("manifest schema_version must be '1.0'")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise BenchmarkError("manifest entries must be a non-empty list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not REQUIRED <= set(entry):
            raise BenchmarkError("each entry needs complete provenance and review fields")
        if entry["entry_id"] in seen:
            raise BenchmarkError(f"duplicate entry_id: {entry['entry_id']}")
        seen.add(entry["entry_id"])
        if entry["expected_shape"] not in SHAPES:
            raise BenchmarkError(f"unsupported expected shape: {entry['expected_shape']}")
        if not str(entry["source_url"]).startswith("https://github.com/"):
            raise BenchmarkError(f"source_url must be a GitHub URL: {entry['entry_id']}")
        if len(str(entry["commit"])) < 7 or not entry["reviewed_excerpt"].strip():
            raise BenchmarkError(f"unfrozen provenance: {entry['entry_id']}")
        source = FIXTURE_ROOT / str(entry["source_file"])
        if not source.is_file():
            raise BenchmarkError(f"missing frozen source: {entry['source_file']}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if entry["source_sha256"] != digest:
            raise BenchmarkError(f"source_sha256 mismatch: {entry['entry_id']}")
        if entry["provenance_status"] not in {"external_exact", "synthetic"}:
            raise BenchmarkError(f"unsupported provenance_status: {entry['entry_id']}")
        if entry["provenance_status"] == "external_exact" and not entry["raw_url"]:
            raise BenchmarkError(f"external_exact entry needs raw_url: {entry['entry_id']}")
        if entry["provenance_status"] == "external_exact":
            if not isinstance(entry.get("upstream_line_start"), int) or not isinstance(entry.get("upstream_line_end"), int) or entry["upstream_line_start"] > entry["upstream_line_end"]:
                raise BenchmarkError(f"external_exact entry needs valid upstream line span: {entry['entry_id']}")
            if entry.get("extraction_mode") == "notebook_cell" and not entry.get("notebook_cell_id"):
                raise BenchmarkError(f"notebook entry needs notebook_cell_id: {entry['entry_id']}")
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict) or not all(k in thresholds for k in ("precision_min", "recall_min", "archetype_recall_min")):
        raise BenchmarkError("thresholds are required")
    return data


def evaluate(manifest: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for entry in manifest["entries"]:
        source = FIXTURE_ROOT / entry["source_file"]
        result = scanner.scan([source], system_id=entry["entry_id"])
        predicted = sorted({item["type"] for item in result["opportunities"]}) or ["not_a_decision"]
        expected = entry["expected_shape"]
        expected_bounded = expected == "bounded_semantic_decision"
        predicted_bounded = "bounded_semantic_decision" in predicted
        rows.append({"entry_id": entry["entry_id"], "archetype": entry["archetype"], "provenance_status": entry["provenance_status"], "expected": expected, "predicted": predicted, "expected_bounded": expected_bounded, "predicted_bounded": predicted_bounded, "match": expected == predicted[0] if len(predicted) == 1 else expected in predicted})
    external_rows = [row for row in rows if row["provenance_status"] == "external_exact"]
    synthetic_rows = [row for row in rows if row["provenance_status"] == "synthetic"]

    def matrix(items: list[dict[str, Any]]) -> dict[str, Any]:
        tp = sum(row["expected_bounded"] and row["predicted_bounded"] for row in items)
        fp = sum(not row["expected_bounded"] and row["predicted_bounded"] for row in items)
        fn = sum(row["expected_bounded"] and not row["predicted_bounded"] for row in items)
        tn = sum(not row["expected_bounded"] and not row["predicted_bounded"] for row in items)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        specificity = tn / (tn + fp) if tn + fp else None
        return {"true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn, "precision": precision, "recall": recall, "specificity": specificity, "total": len(items)}

    external = matrix(external_rows)
    synthetic = matrix(synthetic_rows)
    tp = sum(row["expected_bounded"] and row["predicted_bounded"] for row in rows)
    fp = sum(not row["expected_bounded"] and row["predicted_bounded"] for row in rows)
    fn = sum(row["expected_bounded"] and not row["predicted_bounded"] for row in rows)
    tn = sum(not row["expected_bounded"] and not row["predicted_bounded"] for row in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else None
    by_arch: dict[str, dict[str, Any]] = {}
    for archetype in sorted({row["archetype"] for row in rows}):
        group = [row for row in rows if row["archetype"] == archetype]
        hits = sum(row["expected_bounded"] == row["predicted_bounded"] for row in group)
        by_arch[archetype] = {"total": len(group), "correct": hits, "recall": hits / len(group)}
    thresholds = manifest["thresholds"]
    external_positive = sum(row["expected_bounded"] for row in external_rows)
    external_negative = len(external_rows) - external_positive
    positive_llm_ecosystems = {str(entry.get("provider_ecosystem")) for entry in manifest["entries"] if entry.get("provenance_status") == "external_exact" and entry.get("expected_shape") == "bounded_semantic_decision" and entry.get("provider_ecosystem") not in {"workflow", "document", None}}
    repositories = {str(row.get("source_url", "")).split("github.com/", 1)[-1].split("/", 2)[0] for row in manifest["entries"] if row["provenance_status"] == "external_exact"}
    ecosystems = {str(row.get("provider_ecosystem", "unknown")) for row in manifest["entries"] if row["provenance_status"] == "external_exact"}
    gate_pass = external["total"] >= 10 and external_positive >= 4 and external_negative >= 4 and len(repositories) >= 3 and len(ecosystems - {"unknown"}) >= 2 and len(positive_llm_ecosystems) >= 2 and external["precision"] is not None and external["recall"] is not None and external["specificity"] is not None and external["precision"] >= thresholds["precision_min"] and external["recall"] >= thresholds["recall_min"] and external["specificity"] >= thresholds["specificity_min"]
    gaps = [row["entry_id"] for row in rows if row["expected_bounded"] != row["predicted_bounded"]]
    if external["total"] < 10 or external_positive < 4 or external_negative < 4:
        gaps.append("external_v1_2_coverage_incomplete")
    return {"entries": rows, "confusion": {"true_positive": tp, "false_negative": fn, "false_positive": fp, "true_negative": tn}, "precision": precision, "recall": recall, "specificity": specificity, "external": external | {"positive": external_positive, "negative": external_negative, "repositories": len(repositories), "ecosystems": sorted(ecosystems - {"unknown"}), "positive_llm_ecosystems": sorted(positive_llm_ecosystems)}, "synthetic": synthetic, "by_archetype": by_arch, "gate": "PASS" if gate_pass else "HOLD", "product_readiness": "PASS" if gate_pass else "HOLD", "known_gaps": gaps}


def verify_network(manifest: dict[str, Any]) -> list[str]:
    """Optionally verify external_exact fixtures against pinned raw GitHub URLs."""
    failures: list[str] = []
    for entry in manifest["entries"]:
        if entry["provenance_status"] != "external_exact":
            continue
        try:
            raw_bytes = urllib.request.urlopen(entry["raw_url"], timeout=20).read()
            if entry.get("extraction_mode") == "notebook_cell":
                if hashlib.sha256(raw_bytes).hexdigest() != entry["raw_file_sha256"]:
                    failures.append(entry["entry_id"] + ": raw_file_sha256")
                    continue
                notebook = json.loads(raw_bytes.decode("utf-8"))
                cell = next((item for item in notebook.get("cells", []) if item.get("id") == entry["notebook_cell_id"]), None)
                if cell is None:
                    failures.append(entry["entry_id"] + ": cell not found")
                    continue
                excerpt = "".join(cell.get("source", [])).encode("utf-8")
            else:
                upstream = raw_bytes.decode("utf-8").splitlines()
                excerpt = ("\n".join(upstream[entry["upstream_line_start"] - 1:entry["upstream_line_end"]]) + "\n").encode("utf-8")
            local = (FIXTURE_ROOT / entry["source_file"]).read_bytes()
            if excerpt != local or hashlib.sha256(excerpt).hexdigest() != entry["source_sha256"]:
                failures.append(entry["entry_id"])
        except Exception as exc:  # pragma: no cover - network-only path
            failures.append(f"{entry['entry_id']}: {exc}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verify-network", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if args.verify_network:
            failures = verify_network(manifest)
            if failures:
                print("PROVENANCE: FAIL - " + ", ".join(failures))
                return 2
            print("PROVENANCE: PASS")
        report = evaluate(manifest)
    except BenchmarkError as exc:
        print(f"CORPUS: FAIL - {exc}")
        return 2
    print("CORPUS: PASS")
    if args.validate_only:
        return 0
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"CONFUSION: {report['confusion']}")
        print(f"PRECISION: {report['precision']:.3f}")
        print(f"RECALL: {report['recall']:.3f}")
        print(f"SPECIFICITY: {report['specificity']:.3f}" if report["specificity"] is not None else "SPECIFICITY: undefined")
        print(f"EXTERNAL: {report['external']}")
        print(f"SYNTHETIC: {report['synthetic']}")
        for name, result in report["by_archetype"].items():
            print(f"ARCHETYPE {name}: {result['correct']}/{result['total']} recall={result['recall']:.3f}")
        print(f"BENCHMARK: {report['gate']}")
        print(f"PRODUCT READINESS: {report['product_readiness']}")
        print("KNOWN GAPS: " + (", ".join(report["known_gaps"]) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
