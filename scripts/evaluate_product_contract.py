#!/usr/bin/env python3
"""Run the executable acceptance corpus for the Forge product contract."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT / "docs" / "product-target.md"
DEFAULT_MATRIX = ROOT / "tests" / "fixtures" / "product-contract" / "scenarios.yaml"
TARGET_MARKERS = (
    "product-contract:v1",
    "## Product promise",
    "## Canonical lifecycle",
    "## Required product outputs",
    "## Decision migration contract",
    "## Evidence and authority invariants",
    "## Acceptance corpus",
    "## Confidence ladder",
    "## Release criteria",
    "## Deliberate boundaries",
)
REQUIRED_ARCHETYPES = {
    "source_code",
    "agentic_workflow",
    "documents_only",
    "mixed_material",
    "expensive_llm_call",
    "resolved_history",
}
ALLOWED_MODES = {
    "cold_scan",
    "cold_scan_and_select",
    "cold_scan_with_proposal",
    "llm_call_audit",
    "historical_discovery",
}
FORBIDDEN_OPPORTUNITY_KEYS = {"binding", "executable", "handler", "production"}

sys.path.insert(0, str(ROOT / "scripts"))
import forge  # noqa: E402
import scan_decision_opportunities as opportunity_scan  # noqa: E402
import scan_llm_opportunities as llm_scan  # noqa: E402


class ContractError(ValueError):
    """A target, scenario, or product output violates the acceptance contract."""


def _mapping(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{description} must be a mapping")
    return value


def _repo_path(value: Any, description: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{description} must be a non-empty repository-relative path")
    path = (ROOT / value).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ContractError(f"{description} escapes the repository: {value}")
    if not path.exists():
        raise ContractError(f"{description} does not exist: {value}")
    return path


def validate_target(path: Path = DEFAULT_TARGET) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return list(TARGET_MARKERS)
    return [marker for marker in TARGET_MARKERS if marker not in text]


def load_matrix(path: Path = DEFAULT_MATRIX) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot read product scenario matrix {path}: {exc}") from exc
    return _mapping(document, "product scenario matrix")


def validate_matrix(matrix: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if matrix.get("schema_version") != "1.0":
        problems.append("schema_version must be '1.0'")
    declared = matrix.get("required_archetypes")
    if not isinstance(declared, list) or set(declared) != REQUIRED_ARCHETYPES:
        problems.append("required_archetypes must declare the complete product corpus")
    scenarios = matrix.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        return problems + ["scenarios must be a non-empty list"]
    identifiers: set[str] = set()
    covered: set[str] = set()
    for index, raw in enumerate(scenarios):
        if not isinstance(raw, dict):
            problems.append(f"scenario {index} must be a mapping")
            continue
        identifier = raw.get("scenario_id")
        if not isinstance(identifier, str) or not identifier:
            problems.append(f"scenario {index} needs scenario_id")
        elif identifier in identifiers:
            problems.append(f"duplicate scenario_id: {identifier}")
        else:
            identifiers.add(identifier)
        archetype = raw.get("archetype")
        if archetype not in REQUIRED_ARCHETYPES:
            problems.append(f"scenario {identifier!r} has unknown archetype {archetype!r}")
        else:
            covered.add(str(archetype))
        if raw.get("mode") not in ALLOWED_MODES:
            problems.append(f"scenario {identifier!r} has unknown mode {raw.get('mode')!r}")
        if not isinstance(raw.get("expected"), dict):
            problems.append(f"scenario {identifier!r} needs expected outcomes")
    missing = sorted(REQUIRED_ARCHETYPES - covered)
    if missing:
        problems.append("missing archetypes: " + ", ".join(missing))
    return problems


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ContractError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_subset(actual: Any, expected: Any, label: str) -> None:
    actual_set = set(actual or [])
    expected_set = set(expected or [])
    missing = sorted(expected_set - actual_set)
    if missing:
        raise ContractError(f"{label}: missing {missing!r}; actual={sorted(actual_set)!r}")


def _proposal(raw: Mapping[str, Any], sources: list[Path]) -> dict[str, Any]:
    inventory = opportunity_scan.inventory_sources(sources)
    cited_path = _repo_path(raw.get("source"), "proposal source")
    cited = next(
        (item for item in inventory if Path(str(item["locator"])).resolve() == cited_path),
        None,
    )
    if cited is None:
        raise ContractError(f"proposal source was not inventoried: {cited_path}")
    return {
        "opportunity_id": raw.get("opportunity_id"),
        "type": raw.get("type"),
        "actor_type": raw.get("actor_type"),
        "candidate_actions": raw.get("candidate_actions"),
        "evidence": [
            {
                "source_id": cited["source_id"],
                "line_start": raw.get("line_start"),
                "line_end": raw.get("line_end"),
            }
        ],
    }


def _assert_hypothesis_safety(opportunities: list[Mapping[str, Any]]) -> None:
    for item in opportunities:
        _assert_equal(item.get("maturity"), "hypothesis", "opportunity maturity")
        _assert_equal(item.get("shadow_eligible"), False, "shadow eligibility")
        forbidden = FORBIDDEN_OPPORTUNITY_KEYS.intersection(item)
        if forbidden:
            raise ContractError(f"cold-start opportunity contains forbidden keys: {sorted(forbidden)}")


def _run_cold_scenario(scenario: Mapping[str, Any], workspace: Path) -> None:
    identifier = str(scenario["scenario_id"])
    sources = [_repo_path(item, f"{identifier} source") for item in scenario.get("sources", [])]
    proposals = None
    if scenario["mode"] == "cold_scan_with_proposal":
        proposals = [_proposal(_mapping(scenario.get("proposal"), "proposal"), sources)]
    project = workspace / identifier
    manifest = forge.scan_project(
        project=project,
        sources=sources,
        system_id=str(scenario.get("system_id", identifier)),
        proposals=proposals,
    )
    opportunity_map = yaml.safe_load(
        (project / "decision_opportunity_map.yaml").read_text(encoding="utf-8")
    )
    opportunities = opportunity_map.get("opportunities") or []
    _assert_hypothesis_safety(opportunities)
    expected = _mapping(scenario["expected"], "expected")
    _assert_subset(
        [item.get("opportunity_id") for item in opportunities],
        expected.get("opportunity_ids", []),
        "opportunity ids",
    )
    _assert_subset(
        [item.get("type") for item in opportunities],
        expected.get("opportunity_types", []),
        "opportunity types",
    )
    if expected.get("candidate_actions"):
        vocabularies = [item.get("candidate_actions", []) for item in opportunities]
        wanted = sorted(expected["candidate_actions"])
        if not any(sorted(vocabulary) == wanted for vocabulary in vocabularies):
            raise ContractError(f"candidate action vocabulary {wanted!r} was not discovered")
    source_inventory = yaml.safe_load(
        (project / "evidence" / "source_inventory.yaml").read_text(encoding="utf-8")
    )
    _assert_subset(
        [item.get("kind") for item in source_inventory.get("sources", [])],
        expected.get("source_kinds", []),
        "source kinds",
    )
    if scenario["mode"] == "cold_scan_and_select":
        manifest = forge.select_opportunity(project, opportunity_id=str(scenario["select"]))
        specification = yaml.safe_load(
            (project / "instrumentation_spec.yaml").read_text(encoding="utf-8")
        )
        safety = specification.get("safety") or {}
        for field in ("modifies_source_application", "calls_jev", "executes_actions"):
            _assert_equal(safety.get(field), False, f"instrumentation {field}")
        _assert_equal(safety.get("observation_only"), True, "observation-only scaffold")
        minimum = int(expected.get("minimum_capture_points", 0))
        actual = len((specification.get("integration") or {}).get("suggested_capture_points") or [])
        if actual < minimum:
            raise ContractError(f"capture points: expected at least {minimum}, got {actual}")
    _assert_equal(manifest.get("stage"), expected.get("stage"), "product stage")


def _run_llm_audit(scenario: Mapping[str, Any]) -> None:
    source = _repo_path(scenario.get("source"), "LLM audit source")
    candidates = llm_scan.scan_file(source, source.parent)
    expected = _mapping(scenario["expected"], "expected")
    minimum = int(expected.get("minimum_candidates", 1))
    if len(candidates) < minimum:
        raise ContractError(f"LLM audit expected at least {minimum} candidate(s), got {len(candidates)}")
    matching = [
        item
        for item in candidates
        if all(item.get(key) == value for key, value in expected.items() if key != "minimum_candidates")
    ]
    if not matching:
        raise ContractError("LLM audit did not produce a candidate matching the declared contract")


def _run_historical_scenario(scenario: Mapping[str, Any], workspace: Path) -> None:
    identifier = str(scenario["scenario_id"])
    manifest = forge.discover_project(
        project=workspace / identifier,
        cases=_repo_path(scenario.get("cases"), "resolved cases"),
        sources=[_repo_path(item, f"{identifier} source") for item in scenario.get("sources", [])],
        system_id=str(scenario.get("system_id", identifier)),
        surface_field=scenario.get("surface_field"),
    )
    expected = _mapping(scenario["expected"], "expected")
    _assert_equal(manifest.get("stage"), expected.get("stage"), "historical product stage")
    _assert_equal(
        manifest.get("selected_surface"), expected.get("selected_surface"), "selected surface"
    )
    _assert_equal(manifest.get("safety", {}).get("executes_actions"), False, "action execution")
    _assert_equal(
        manifest.get("safety", {}).get("production_authority"), False, "production authority"
    )


def run_scenario(scenario: Mapping[str, Any], workspace: Path) -> None:
    mode = scenario.get("mode")
    if mode in {"cold_scan", "cold_scan_and_select", "cold_scan_with_proposal"}:
        _run_cold_scenario(scenario, workspace)
    elif mode == "llm_call_audit":
        _run_llm_audit(scenario)
    elif mode == "historical_discovery":
        _run_historical_scenario(scenario, workspace)
    else:
        raise ContractError(f"unsupported scenario mode: {mode!r}")


def evaluate_matrix(matrix: Mapping[str, Any], workspace: Path) -> list[dict[str, str]]:
    problems = validate_matrix(matrix)
    if problems:
        raise ContractError("invalid scenario matrix: " + "; ".join(problems))
    workspace.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    for scenario in matrix["scenarios"]:
        identifier = str(scenario["scenario_id"])
        try:
            run_scenario(scenario, workspace)
        except Exception as exc:  # each failure must remain visible in the complete matrix
            results.append({"scenario_id": identifier, "status": "fail", "detail": str(exc)})
        else:
            results.append({"scenario_id": identifier, "status": "pass", "detail": "contract met"})
    return results


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    result.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    result.add_argument("--check-target", action="store_true")
    result.add_argument("--list-scenarios", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    target_problems = validate_target(args.target)
    if args.check_target:
        if target_problems:
            print("TARGET CONTRACT: FAIL")
            for problem in target_problems:
                print(f"- missing {problem}")
            return 1
        print("TARGET CONTRACT: PASS")
        return 0
    try:
        matrix = load_matrix(args.matrix)
    except ContractError as exc:
        print(f"SCENARIO COVERAGE: FAIL - {exc}")
        return 1
    matrix_problems = validate_matrix(matrix)
    if args.list_scenarios:
        for scenario in matrix.get("scenarios", []):
            print(f"{scenario.get('scenario_id')}: {scenario.get('archetype')}")
        count = len(matrix.get("scenarios", []))
        if matrix_problems:
            print(f"SCENARIO COVERAGE: FAIL {count}/{len(REQUIRED_ARCHETYPES)}")
            return 1
        print(f"SCENARIO COVERAGE: PASS {count}/{len(REQUIRED_ARCHETYPES)}")
        return 0
    if target_problems or matrix_problems:
        for problem in target_problems:
            print(f"TARGET FAIL: missing {problem}")
        for problem in matrix_problems:
            print(f"MATRIX FAIL: {problem}")
        print("PRODUCT CONTRACT: FAIL")
        return 1
    with tempfile.TemporaryDirectory(prefix="forge-product-contract-") as directory:
        results = evaluate_matrix(matrix, Path(directory))
    for result in results:
        print(f"{result['scenario_id']}: {result['status'].upper()} - {result['detail']}")
    passed = sum(item["status"] == "pass" for item in results)
    if passed != len(results):
        print(f"PRODUCT CONTRACT: FAIL ({passed}/{len(results)})")
        return 1
    print(f"PRODUCT CONTRACT: PASS ({passed}/{len(results)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
