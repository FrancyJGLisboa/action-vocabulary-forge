#!/usr/bin/env python3
"""Discover cold-start decision opportunities without historical cases.

This scanner extracts only structure supported directly by local source files.
It can also validate System 2 proposals, but a proposed bounded action must
appear in the cited source lines. Discovery output is hypothesis-only: it does
not create bindings, authorize actions, or claim shadow readiness.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


SUPPORTED_EXTENSIONS = {".dot", ".json", ".md", ".py", ".ts", ".tsx", ".txt", ".yaml", ".yml"}
IGNORED_PARTS = {
    ".git",
    ".forge",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
ACTION_CONSTANT_SUFFIXES = ("_ACTIONS", "_CHOICES", "_OPTIONS")
BOUNDED_CALLS = {"choice", "choose", "classify", "route", "select"}
GENERATION_CALLS = {"complete", "generate"}
FORBIDDEN_PROPOSAL_KEYS = {"binding", "executable", "handler", "production"}
ALLOWED_ACTOR_TYPES = {"human", "agent", "software", "mixed", "unknown"}


class ScanError(ValueError):
    """Cold-start material or a proposed hypothesis violates the scan contract."""


def slug(value: Any, *, fallback: str = "item") -> str:
    rendered = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_") or fallback
    if rendered[0].isdigit():
        rendered = f"{fallback}_{rendered}"
    return rendered[:64].rstrip("_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_kind(path: Path) -> str:
    if path.suffix.lower() in {".py", ".ts", ".tsx", ".dot"}:
        return "code"
    if path.suffix.lower() in {".json", ".yaml", ".yml"}:
        return "schema_or_data"
    return "document"


def _line_evidence(source: Mapping[str, Any], line: int, kind: str) -> dict[str, Any]:
    return {"source_id": source["source_id"], "line_start": line, "line_end": line, "kind": kind}


def _bounded_labels(text: str) -> list[str]:
    """Extract labels only when text explicitly asks for a bounded choice.

    This deliberately recognizes a small, conservative grammar. A list or a
    prose mention without an explicit choice instruction remains unclassified.
    """
    explicit = re.search(r"\b(?:choose|select|pick|decide|return)\b.{0,80}\b(?:exactly one|one of|from)\b|\b(?:exactly one|one of)\b|\bdecid\w*\s+whether\b", text, re.I)
    if not explicit:
        return []
    quoted = [slug(value) for value in re.findall(r"['\"]([^'\"]+)['\"]", text) if value.strip()]
    if len(set(quoted)) >= 2:
        return sorted(set(quoted))[:12]
    tail = re.search(r"(?:exactly one|one of|from)\s*:?\s*(.*)$", text, re.I)
    label_text = tail.group(1) if tail else text
    labels = re.findall(r"\b(?:ready|accept|approve|approved|revise|revision|reject|escalate|escalation|lgtm|retry|stop|continue|blocked|complete|needs)\b", label_text, re.I)
    return sorted({slug(label) for label in labels})[:12] if len(set(labels)) >= 2 else []


def scan_document(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Find explicit bounded-choice language as a hypothesis, never authority."""
    path = Path(str(source["locator"]))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    result: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        actions = _bounded_labels(line)
        if "structured decision" in line.lower():
            window = "\n".join(lines[number - 1:min(len(lines), number + 5)])
            labels = [slug(value) for value in re.findall(r"label:\s*[\"']([^\"']+)", window, re.I)]
            if len(set(labels)) >= 2:
                result.append(_base_opportunity(
                    f"{path.stem}_bounded_choice", "bounded_semantic_decision", "agent", "observe_then_jev",
                    [_line_evidence(source, number, "structured_decision_labels")],
                ) | {"candidate_actions": sorted(set(labels))[:12]})
                continue
        if len(actions) < 2:
            continue
        result.append(_base_opportunity(
            f"{path.stem}_bounded_choice", "bounded_semantic_decision", "unknown", "observe_then_jev",
            [_line_evidence(source, number, "bounded_choice_prose")],
        ) | {"candidate_actions": actions})
    return result


def scan_typescript(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Find explicit bounded labels in TS without pretending to understand execution."""
    path = Path(str(source["locator"]))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    full_text = "\n".join(lines)
    if re.search(r"\b(?:streamText|generateText|generateObject)\s*\(", full_text) and re.search(r"\bconst\s+tools\s*=\s*\{", full_text):
        tool_names = re.findall(r"^\s{2,}([A-Za-z_$][\w$]*):\s*\w+Tool", full_text, re.M)
        if len(set(tool_names)) >= 3:
            line = next((n for n, value in enumerate(lines, 1) if "streamText(" in value or "generateText(" in value or "generateObject(" in value), 1)
            return [_base_opportunity(
                path.stem + "_model_tool_router", "bounded_semantic_decision", "agent", "observe_then_jev",
                [_line_evidence(source, line, "bounded_model_tool_call")],
            ) | {"candidate_actions": sorted(set(tool_names))}]
    constants: dict[str, tuple[list[str], int]] = {}
    array_re = re.compile(r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*\[([^\]]+)\]")
    string_re = re.compile(r"['\"]([^'\"]+)['\"]")
    for number, line in enumerate(lines, 1):
        match = array_re.search(line)
        if not match:
            continue
        labels = sorted({slug(value) for value in string_re.findall(match.group(2)) if value.strip()})
        if len(labels) >= 2 and re.search(r"(?:ACTION|CHOICE|OPTION|LABEL|OUTCOME)", match.group(1), re.I):
            constants[match.group(1)] = (labels, number)
    opportunities: list[dict[str, Any]] = []
    call_re = re.compile(r"\b(choice|choose|classify|route|select)\s*\([^\n]*\b([A-Za-z_$][\w$]*)\b")
    for number, line in enumerate(lines, 1):
        match = call_re.search(line)
        if not match or match.group(2) not in constants:
            continue
        name = path.stem + "_" + match.group(1)
        actions, constant_line = constants[match.group(2)]
        opportunities.append(_base_opportunity(
            name, "bounded_semantic_decision", "agent", "observe_then_jev",
            [_line_evidence(source, number, "bounded_choice_call"),
             _line_evidence(source, constant_line, "bounded_action_vocabulary")],
        ) | {"candidate_actions": actions})
    return opportunities


def scan_dot(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Find DOT nodes with two or more explicitly labelled outgoing transitions."""
    path = Path(str(source["locator"]))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    edge_re = re.compile(r"\b([A-Za-z_][\w.-]*)\s*->\s*([A-Za-z_][\w.-]*)\s*\[([^]]*)\]")
    label_re = re.compile(r"\b(?:label|on_label)\s*=\s*['\"]([^'\"]+)['\"]", re.I)
    grouped: dict[str, list[tuple[str, int]]] = {}
    for number, line in enumerate(lines, 1):
        edge = edge_re.search(line)
        if not edge:
            continue
        label = label_re.search(edge.group(3))
        if label and label.group(1).strip():
            grouped.setdefault(edge.group(1), []).append((slug(label.group(1)), number))
    result = []
    for node, edges in sorted(grouped.items()):
        actions = sorted({action for action, _ in edges})
        if len(actions) < 2:
            continue
        evidence = [_line_evidence(source, line, "bounded_transition_label") for _, line in edges]
        result.append(_base_opportunity(
            f"{path.stem}_{node}", "bounded_semantic_decision", "agent", "observe_then_jev", evidence,
        ) | {"candidate_actions": actions})
    return result


def inventory_sources(paths: Iterable[str | Path], *, max_bytes: int = 2_000_000) -> list[dict[str, Any]]:
    files: set[Path] = set()
    for supplied in paths:
        path = Path(supplied).expanduser().resolve()
        if not path.exists():
            raise ScanError(f"source does not exist: {supplied}")
        candidates = [path] if path.is_file() else path.rglob("*")
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if any(part in IGNORED_PARTS for part in candidate.parts):
                continue
            if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                if candidate.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            files.add(candidate.resolve())
    result = []
    for path in sorted(files, key=str):
        digest = _sha256(path)
        identity = hashlib.sha256((str(path) + "\0" + digest).encode("utf-8")).hexdigest()[:16]
        result.append(
            {
                "source_id": f"material:{identity}",
                "kind": _source_kind(path),
                "locator": str(path),
                "sha256": digest,
                "bytes": path.stat().st_size,
                "authority": "contextual",
            }
        )
    if not result:
        raise ScanError("no supported source files were found")
    return result


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _string_sequence(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str) or not item.value.strip():
            return None
        values.append(slug(item.value, fallback="action"))
    return sorted(set(values)) if len(set(values)) >= 2 else None


def _constant_assignments(tree: ast.AST) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    action_constants: dict[str, dict[str, Any]] = {}
    numeric_constants: dict[str, dict[str, Any]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            sequence = _string_sequence(value)
            if sequence and target.id.endswith(ACTION_CONSTANT_SUFFIXES):
                action_constants[target.id] = {
                    "actions": sequence,
                    "line_start": getattr(node, "lineno", 1),
                    "line_end": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                }
            if (
                target.id.isupper()
                and isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
                and not isinstance(value.value, bool)
            ):
                numeric_constants[target.id] = {
                    "value": value.value,
                    "line_start": getattr(node, "lineno", 1),
                    "line_end": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                }
    return action_constants, numeric_constants


def _evidence(source: Mapping[str, Any], node: ast.AST, kind: str) -> dict[str, Any]:
    return {
        "source_id": source["source_id"],
        "line_start": getattr(node, "lineno", 1),
        "line_end": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        "kind": kind,
    }


def _base_opportunity(
    opportunity_id: str,
    opportunity_type: str,
    actor_type: str,
    recommended_runtime: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "opportunity_id": slug(opportunity_id),
        "type": opportunity_type,
        "actor_type": actor_type,
        "maturity": "hypothesis",
        "observed_cases": 0,
        "shadow_eligible": False,
        "recommended_runtime": recommended_runtime,
        "evidence": evidence,
        "limitations": [
            "No resolved cases were supplied.",
            "The candidate has not been measured or approved for execution.",
        ],
    }


def scan_python(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = Path(str(source["locator"]))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    action_constants, numeric_constants = _constant_assignments(tree)
    enum_actions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(isinstance(base, ast.Name) and base.id == "Enum" for base in node.bases):
            values = [item.value.value for item in node.body if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)]
            if 2 <= len(values) <= 12:
                enum_actions.extend(slug(value) for value in values)
    opportunities: list[dict[str, Any]] = []
    used_rules: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
        for name in sorted(names & numeric_constants.keys()):
            if name in used_rules:
                continue
            used_rules.add(name)
            opportunity = _base_opportunity(
                name.lower(),
                "deterministic_rule",
                "software",
                "code",
                [_evidence(source, node, "exact_comparison")],
            )
            opportunity["rule_value"] = numeric_constants[name]["value"]
            opportunities.append(opportunity)

    for function in [item for item in ast.walk(tree) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        calls = [item for item in ast.walk(function) if isinstance(item, ast.Call)]
        model_calls = [item for item in calls if _call_name(item.func).endswith("completions.create")]
        if enum_actions and model_calls:
            opportunities.append(_base_opportunity(
                function.name, "bounded_semantic_decision", "agent", "observe_then_jev",
                [_evidence(source, model_calls[0], "bounded_model_call")],
            ) | {"candidate_actions": sorted(set(enum_actions))})
            continue
        bounded_calls = [item for item in calls if _call_name(item.func).split(".")[-1] in BOUNDED_CALLS]
        actions: list[str] = []
        constant_evidence: list[dict[str, Any]] = []
        for call in bounded_calls:
            referenced = {
                item.id
                for argument in call.args
                for item in ast.walk(argument)
                if isinstance(item, ast.Name)
            }
            for name in sorted(referenced & action_constants.keys()):
                actions.extend(action_constants[name]["actions"])
                constant_evidence.append(
                    {
                        "source_id": source["source_id"],
                        "line_start": action_constants[name]["line_start"],
                        "line_end": action_constants[name]["line_end"],
                        "kind": "bounded_action_vocabulary",
                    }
                )
        if bounded_calls and len(set(actions)) >= 2:
            opportunity = _base_opportunity(
                function.name,
                "bounded_semantic_decision",
                "agent",
                "observe_then_jev",
                [_evidence(source, bounded_calls[0], "bounded_choice_call"), *constant_evidence],
            )
            opportunity["candidate_actions"] = sorted(set(actions))
            opportunities.append(opportunity)
            continue

        generation_calls = [
            item for item in calls if _call_name(item.func).split(".")[-1] in GENERATION_CALLS
        ]
        if generation_calls:
            call = generation_calls[0]
            line = getattr(call, "lineno", 1)
            source_lines = Path(str(source["locator"])).read_text(encoding="utf-8").splitlines()
            source_line = "\n".join(source_lines[line - 1:min(len(source_lines), line + 4)])
            bounded_actions = _bounded_labels(source_line)
            if len(bounded_actions) >= 2:
                opportunities.append(
                    _base_opportunity(
                        function.name, "bounded_semantic_decision", "agent", "observe_then_jev",
                        [_evidence(source, call, "bounded_generation_call")],
                    ) | {"candidate_actions": bounded_actions}
                )
                continue
            opportunities.append(
                _base_opportunity(
                    function.name,
                    "open_generation",
                    "agent",
                    "llm",
                    [_evidence(source, generation_calls[0], "generation_call")],
                )
            )
    return opportunities


def _source_lines(source: Mapping[str, Any]) -> list[str]:
    try:
        path = Path(str(source["locator"]))
        if _sha256(path) != source.get("sha256"):
            raise ScanError(f"cited source changed after inventory: {source.get('source_id')}")
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ScanError(f"cannot read cited source {source.get('source_id')}: {exc}") from exc


def _action_appears(action: str, text: str) -> bool:
    token_pattern = r"[\s_-]+".join(re.escape(token) for token in action.split("_") if token)
    return bool(
        token_pattern
        and re.search(
            rf"(?<![A-Za-z0-9_]){token_pattern}(?![A-Za-z0-9_])",
            text,
            re.IGNORECASE,
        )
    )


def validate_proposals(
    proposals: Iterable[Mapping[str, Any]],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate System 2 hypotheses against exact cited source lines."""
    by_id = {item["source_id"]: item for item in sources}
    allowed_types = {"bounded_semantic_decision", "deterministic_rule", "open_generation"}
    normalized = []
    for index, raw in enumerate(proposals, 1):
        if not isinstance(raw, Mapping):
            raise ScanError(f"proposal {index}: each opportunity must be a mapping")
        if any(key in raw for key in FORBIDDEN_PROPOSAL_KEYS):
            raise ScanError(f"proposal {index}: executable or production fields are forbidden")
        raw_opportunity_id = str(raw.get("opportunity_id") or "").strip()
        if not raw_opportunity_id:
            raise ScanError(f"proposal {index}: opportunity_id is required")
        opportunity_id = slug(raw_opportunity_id)
        opportunity_type = raw.get("type")
        if opportunity_type not in allowed_types:
            raise ScanError(f"proposal {opportunity_id}: unsupported type {opportunity_type!r}")
        actor_type = raw.get("actor_type") or "unknown"
        if actor_type not in ALLOWED_ACTOR_TYPES:
            raise ScanError(f"proposal {opportunity_id}: unsupported actor_type {actor_type!r}")
        evidence = raw.get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            raise ScanError(f"proposal {opportunity_id}: source evidence is required")
        snippets = []
        normalized_evidence = []
        for claim in evidence:
            if not isinstance(claim, Mapping) or claim.get("source_id") not in by_id:
                raise ScanError(f"proposal {opportunity_id}: evidence references an unknown source")
            start = claim.get("line_start")
            end = claim.get("line_end", start)
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                raise ScanError(f"proposal {opportunity_id}: invalid evidence line range")
            lines = _source_lines(by_id[claim["source_id"]])
            if end > len(lines):
                raise ScanError(f"proposal {opportunity_id}: evidence line range exceeds the source")
            snippets.append("\n".join(lines[start - 1:end]))
            normalized_evidence.append(
                {
                    "source_id": claim["source_id"],
                    "line_start": start,
                    "line_end": end,
                    "kind": "system_two_proposal",
                }
            )
        raw_actions = raw.get("candidate_actions", [])
        if not isinstance(raw_actions, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_actions
        ):
            raise ScanError(f"proposal {opportunity_id}: candidate_actions must be a list of strings")
        actions = sorted({slug(item, fallback="action") for item in raw_actions})
        if opportunity_type == "bounded_semantic_decision":
            if len(actions) < 2:
                raise ScanError(f"proposal {opportunity_id}: bounded decisions need at least two actions")
            combined = "\n".join(snippets)
            for action in actions:
                if not _action_appears(action, combined):
                    raise ScanError(f"proposal {opportunity_id}: unsupported action {action!r} is absent from cited evidence")
        opportunity = _base_opportunity(
            opportunity_id,
            str(opportunity_type),
            str(actor_type),
            {
                "bounded_semantic_decision": "observe_then_jev",
                "deterministic_rule": "code",
                "open_generation": "llm",
            }[str(opportunity_type)],
            normalized_evidence,
        )
        if actions:
            opportunity["candidate_actions"] = actions
        normalized.append(opportunity)
    return normalized


def scan(
    sources: Iterable[str | Path],
    *,
    system_id: str,
    proposals: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    inventory = inventory_sources(sources)
    opportunities = []
    for source in inventory:
        if source["kind"] == "code":
            suffix = Path(source["locator"]).suffix.lower()
            if suffix == ".py":
                opportunities.extend(scan_python(source))
            elif suffix in {".ts", ".tsx"}:
                opportunities.extend(scan_typescript(source))
            elif suffix == ".dot":
                opportunities.extend(scan_dot(source))
        elif source["kind"] == "document":
            opportunities.extend(scan_document(source))
    if proposals is not None:
        opportunities.extend(validate_proposals(proposals, inventory))
    unique: dict[str, dict[str, Any]] = {}
    for opportunity in opportunities:
        opportunity_id = opportunity["opportunity_id"]
        if opportunity_id in unique:
            existing = unique[opportunity_id]
            if existing["type"] != opportunity["type"]:
                raise ScanError(f"conflicting opportunity types for {opportunity_id!r}")
            continue
        unique[opportunity_id] = opportunity
    ordered = sorted(unique.values(), key=lambda item: (item["type"], item["opportunity_id"]))
    return {
        "schema_version": "1.0",
        "system_id": slug(system_id, fallback="scanned_system"),
        "mode": "cold_start",
        "observed_cases": 0,
        "source_inventory": inventory,
        "actors": sorted({item["actor_type"] for item in ordered}),
        "opportunities": ordered,
        "safety": {
            "hypotheses_only": True,
            "shadow_eligible": False,
            "executes_actions": False,
            "bindings_generated": False,
        },
    }
