#!/usr/bin/env python3
"""Verify real runtime observations and compile them through the Forge core."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "observed-runtime-benchmark"
sys.path.insert(0, str(ROOT / "scripts"))
import observe_decision_events as observation  # noqa: E402


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_RUNTIME_FIELDS = {
    "runtime_id",
    "repository",
    "commit",
    "license",
    "license_url",
    "source_snapshot_unmodified",
    "no_paid_model_calls",
    "baseline",
    "capture",
    "opportunity",
    "expected",
}
REQUIRED_CAPTURE_FIELDS = {
    "method",
    "source_locator",
    "source_url",
    "command",
    "result",
    "adapter_file",
    "adapter_sha256",
    "event_file",
    "event_sha256",
    "source_ref_prefix",
}


class ObservedRuntimeBenchmarkError(ValueError):
    """The observed-runtime evidence contract is incomplete or inconsistent."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ObservedRuntimeBenchmarkError(f"cannot read artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _fixture_path(relative: Any, fixture_root: Path) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ObservedRuntimeBenchmarkError("artifact paths must be non-empty strings")
    candidate = (fixture_root / relative).resolve()
    try:
        candidate.relative_to(fixture_root.resolve())
    except ValueError as exc:
        raise ObservedRuntimeBenchmarkError(f"artifact escapes fixture root: {relative}") from exc
    return candidate


def load_manifest(path: Path = FIXTURE_ROOT / "manifest.yaml") -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ObservedRuntimeBenchmarkError(f"cannot read manifest: {exc}") from exc
    return validate_manifest(data, fixture_root=path.parent)


def validate_manifest(data: Any, *, fixture_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != "1.0":
        raise ObservedRuntimeBenchmarkError("manifest schema_version must be '1.0'")
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict) or not all(
        isinstance(thresholds.get(name), int) and thresholds[name] > 0
        for name in (
            "minimum_runtimes",
            "minimum_repositories",
            "minimum_runtime_events",
            "minimum_repeated_path_cases",
        )
    ):
        raise ObservedRuntimeBenchmarkError("positive runtime thresholds are required")
    if thresholds["minimum_repeated_path_cases"] < 2:
        raise ObservedRuntimeBenchmarkError("minimum_repeated_path_cases must be at least 2")

    runtimes = data.get("runtimes")
    if not isinstance(runtimes, list) or not runtimes:
        raise ObservedRuntimeBenchmarkError("runtimes must be a non-empty list")
    seen: set[str] = set()
    for runtime in runtimes:
        if not isinstance(runtime, dict) or not REQUIRED_RUNTIME_FIELDS <= set(runtime):
            raise ObservedRuntimeBenchmarkError("each runtime needs complete execution provenance")
        runtime_id = runtime["runtime_id"]
        if not isinstance(runtime_id, str) or not runtime_id or runtime_id in seen:
            raise ObservedRuntimeBenchmarkError(f"invalid or duplicate runtime_id: {runtime_id!r}")
        seen.add(runtime_id)
        if not str(runtime["repository"]).startswith("https://github.com/"):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: repository must be a GitHub URL")
        if not COMMIT_RE.fullmatch(str(runtime["commit"])):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: commit must be a full SHA")
        if runtime["license"] != "MIT" or not str(runtime["license_url"]).startswith(
            runtime["repository"] + "/blob/" + runtime["commit"]
        ):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: license is not pinned to the commit")
        if runtime["source_snapshot_unmodified"] is not True:
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: pinned source snapshot must remain unmodified")
        if runtime["no_paid_model_calls"] is not True:
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: benchmark must not require paid model calls")

        baseline = runtime["baseline"]
        if not isinstance(baseline, dict) or baseline.get("status") != "passed" or not all(
            isinstance(baseline.get(name), str) and baseline[name].strip()
            for name in ("command", "result")
        ):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: passing baseline execution is required")

        capture = runtime["capture"]
        if not isinstance(capture, dict) or not REQUIRED_CAPTURE_FIELDS <= set(capture):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: capture provenance is incomplete")
        if not str(capture["source_url"]).startswith(runtime["repository"] + "/blob/" + runtime["commit"]):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: capture source is not commit-pinned")
        artifact_pairs = [
            (capture["adapter_file"], capture["adapter_sha256"]),
            (capture["event_file"], capture["event_sha256"]),
        ]
        if "integration_patch_file" in capture or "integration_patch_sha256" in capture:
            if not all(name in capture for name in ("integration_patch_file", "integration_patch_sha256")):
                raise ObservedRuntimeBenchmarkError(f"{runtime_id}: integration patch provenance is incomplete")
            artifact_pairs.append((capture["integration_patch_file"], capture["integration_patch_sha256"]))
        for relative, expected_digest in artifact_pairs:
            artifact = _fixture_path(relative, fixture_root)
            if not artifact.is_file() or sha256_file(artifact) != expected_digest:
                raise ObservedRuntimeBenchmarkError(f"{runtime_id}: artifact hash mismatch for {relative}")

        opportunity = runtime["opportunity"]
        actions = opportunity.get("candidate_actions") if isinstance(opportunity, dict) else None
        if (
            not isinstance(opportunity, dict)
            or not isinstance(opportunity.get("opportunity_id"), str)
            or opportunity.get("actor_type") not in observation.ALLOWED_EVENT_ACTORS
            or not isinstance(actions, list)
            or len(actions) < 2
            or len(set(actions)) != len(actions)
            or not all(isinstance(action, str) and action.strip() for action in actions)
            or not isinstance(opportunity.get("discovery_evidence"), str)
        ):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: bounded opportunity is incomplete")
        expected = runtime["expected"]
        if not isinstance(expected, dict) or not all(
            isinstance(expected.get(name), int) and expected[name] >= 0
            for name in ("minimum_events", "minimum_cases", "minimum_repeated_paths")
        ):
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: expected observation counts are required")
        if expected["minimum_events"] < thresholds["minimum_runtime_events"]:
            raise ObservedRuntimeBenchmarkError(f"{runtime_id}: minimum event expectation is below the benchmark threshold")
    return data


def runtime_coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    runtimes = manifest["runtimes"]
    repositories = {runtime["repository"] for runtime in runtimes}
    thresholds = manifest["thresholds"]
    passed = (
        len(runtimes) >= thresholds["minimum_runtimes"]
        and len(repositories) >= thresholds["minimum_repositories"]
        and all(runtime["no_paid_model_calls"] and runtime["baseline"]["status"] == "passed" for runtime in runtimes)
    )
    return {
        "status": "PASS" if passed else "HOLD",
        "runtimes": len(runtimes),
        "repositories": len(repositories),
    }


def verify_provenance(manifest: Mapping[str, Any], *, fixture_root: Path = FIXTURE_ROOT) -> list[str]:
    failures: list[str] = []
    for runtime in manifest["runtimes"]:
        runtime_id = runtime["runtime_id"]
        capture = runtime["capture"]
        events = observation.load_events(_fixture_path(capture["event_file"], fixture_root))
        if not events or any(
            not str(event.get("source_ref", "")).startswith(capture["source_ref_prefix"])
            for event in events
        ):
            failures.append(f"{runtime_id}: event source_ref does not match capture provenance")
        result_text = str(capture["result"])
        if str(len(events)) not in result_text:
            failures.append(f"{runtime_id}: capture result does not attest the frozen event count")
        if capture["method"] == "opt_in_local_adapter_on_separate_worktree" and "integration_patch_file" not in capture:
            failures.append(f"{runtime_id}: local adapter is missing its integration patch")
        if capture["method"] == "opt_in_local_adapter_on_separate_worktree":
            setup_commands = capture.get("setup_commands")
            if (
                not isinstance(setup_commands, list)
                or len(setup_commands) < 2
                or not all(isinstance(command, str) and command.strip() for command in setup_commands)
                or not any("{adapter_file}" in command for command in setup_commands)
                or not any("{integration_patch_file}" in command for command in setup_commands)
            ):
                failures.append(f"{runtime_id}: local adapter setup is not reproducible")
    return failures


def verify_safety(manifest: Mapping[str, Any], *, fixture_root: Path = FIXTURE_ROOT) -> list[str]:
    failures: list[str] = []
    for runtime in manifest["runtimes"]:
        try:
            events = observation.load_events(_fixture_path(runtime["capture"]["event_file"], fixture_root))
            observation.validate_events(events, runtime["opportunity"])
        except (observation.ObservationError, ObservedRuntimeBenchmarkError) as exc:
            failures.append(f"{runtime['runtime_id']}: {exc}")
    return failures


def evaluate(manifest: Mapping[str, Any], *, fixture_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    runtime_results: list[dict[str, Any]] = []
    failures: list[str] = []
    total_repeated_paths = 0
    for runtime in manifest["runtimes"]:
        runtime_id = runtime["runtime_id"]
        expected = runtime["expected"]
        compiled = observation.observe(
            _fixture_path(runtime["capture"]["event_file"], fixture_root),
            runtime["opportunity"],
            system_id=f"observed-runtime:{runtime_id}",
            minimum_events=expected["minimum_events"],
            minimum_path_cases=manifest["thresholds"]["minimum_repeated_path_cases"],
        )
        summary = compiled["observation_manifest"]["summary"]
        decision_map = compiled["decision_system_map"]
        total_repeated_paths += summary["repeated_paths"]
        checks = {
            "events": summary["events"] >= expected["minimum_events"],
            "cases": summary["cases"] >= expected["minimum_cases"],
            "repeated_paths": summary["repeated_paths"] >= expected["minimum_repeated_paths"],
            "descriptive_only": decision_map["safety"]["descriptive_only"] is True,
            "executes_actions": decision_map["safety"]["executes_actions"] is False,
            "raw_state_persisted": decision_map["safety"]["raw_event_state_persisted"] is False,
        }
        if not all(checks.values()):
            failures.append(runtime_id)
        runtime_results.append(
            {
                "runtime_id": runtime_id,
                "events": summary["events"],
                "cases": summary["cases"],
                "repeated_paths": summary["repeated_paths"],
                "workflow_map_ready": decision_map["readiness"]["workflow_map_ready"],
                "checks": checks,
            }
        )
    coverage = runtime_coverage(manifest)
    provenance_failures = verify_provenance(manifest, fixture_root=fixture_root)
    safety_failures = verify_safety(manifest, fixture_root=fixture_root)
    passed = not failures and not provenance_failures and not safety_failures and coverage["status"] == "PASS"
    return {
        "status": "PASS" if passed else "HOLD",
        "coverage": coverage,
        "runtimes": runtime_results,
        "total_repeated_paths": total_repeated_paths,
        "failures": failures + provenance_failures + safety_failures,
    }


def _print_runtime_rows(manifest: Mapping[str, Any]) -> None:
    for runtime in manifest["runtimes"]:
        print(
            f"{runtime['runtime_id']}: {runtime['repository']}@{runtime['commit']} "
            f"license={runtime['license']} baseline={runtime['baseline']['status']} paid_calls=no"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list-runtimes", action="store_true")
    mode.add_argument("--verify-provenance", action="store_true")
    mode.add_argument("--verify-safety", action="store_true")
    mode.add_argument("--require-repeated-path", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if args.list_runtimes:
            coverage = runtime_coverage(manifest)
            _print_runtime_rows(manifest)
            print(f"RUNTIME COVERAGE: {coverage['status']}")
            return 0 if coverage["status"] == "PASS" else 2
        if args.verify_provenance:
            failures = verify_provenance(manifest)
            print("RUNTIME PROVENANCE: " + ("PASS" if not failures else "FAIL - " + "; ".join(failures)))
            return 0 if not failures else 2
        if args.verify_safety:
            failures = verify_safety(manifest)
            print("OBSERVATION SAFETY: " + ("PASS" if not failures else "FAIL - " + "; ".join(failures)))
            return 0 if not failures else 2
        report = evaluate(manifest)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            for runtime in report["runtimes"]:
                print(
                    f"{runtime['runtime_id']}: events={runtime['events']} cases={runtime['cases']} "
                    f"repeated_paths={runtime['repeated_paths']} map_ready={runtime['workflow_map_ready']}"
                )
            if args.require_repeated_path:
                status = "PASS" if report["status"] == "PASS" and report["total_repeated_paths"] > 0 else "HOLD"
                print(f"REPEATED PATH EVIDENCE: {status}")
                return 0 if status == "PASS" else 2
            print(f"OBSERVED RUNTIME BENCHMARK: {report['status']}")
        return 0 if report["status"] == "PASS" else 2
    except (ObservedRuntimeBenchmarkError, observation.ObservationError) as exc:
        print(f"OBSERVED RUNTIME BENCHMARK: FAIL - {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
