#!/usr/bin/env python3
"""Find code locations that may contain bounded decisions implemented by generative calls."""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Iterable

import yaml


CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".rb", ".php",
}
SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
MODEL_PROVIDER_PATTERN = re.compile(
    r"api[.]openai[.]com|api[.]anthropic[.]com|anthropic|claude|openai",
    re.IGNORECASE,
)
DIRECT_CALL_PATTERN = re.compile(
    r"\b(?P<call>(?:[A-Za-z_$][\w$]*[.])*(?:"
    r"chat[.]completions[.](?:create|parse)|"
    r"responses[.](?:create|parse)|"
    r"messages[.](?:create|stream)|"
    r"model[.]invoke|llm[.]invoke|"
    r"generate_text|generateText|completion|"
    r"urllib[.]request[.]urlopen|urlopen|"
    r"requests[.](?:post|request)|httpx[.](?:post|request)|"
    r"fetch|axios[.](?:post|request)"
    r"))\s*[(]",
)
SEMANTIC_PATTERN = re.compile(
    r"classif|categor|choose|select|route|decid|approve|reject|retry|"
    r"escalat|triage|finite|one of|next action",
    re.IGNORECASE,
)
QUOTED_TOKEN_PATTERN = re.compile(r"""['"]([A-Za-z][A-Za-z0-9_-]{1,40})['"]""")
BULLET_PATTERN = re.compile(r"^\s*[-*]\s*([A-Za-z][A-Za-z0-9_ -]{1,40})\s*$")
BARE_OPTIONS_PATTERN = re.compile(
    r"(?:choose|select|route)(?:\s+one)?\s*:?\s*"
    r"([A-Za-z][A-Za-z0-9_-]*(?:\s*,\s*[A-Za-z][A-Za-z0-9_-]*){1,11})",
    re.IGNORECASE,
)
STOPWORDS = {
    "the", "this", "that", "true", "false", "none", "null", "string",
    "system", "user", "assistant", "json", "object", "content", "role",
    "model", "message", "input", "output", "context", "response",
}
DECISION_SHAPES = {"bounded_semantic_decision", "generative_task", "deterministic_rule", "insufficient_evidence"}
READINESS_SPEC = (("poor_fit", "open-ended or insufficiently evidenced; not suitable"), ("investigate", "plausible bounded decision but needs review"), ("shadow_ready", "clear finite decision suitable for shadow evaluation"))
READINESS = [label for label, _ in READINESS_SPEC]
DEFAULT_JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        yield path


def extract_options(lines: list[str]) -> list[str]:
    options: list[str] = []
    for line in lines:
        for token in QUOTED_TOKEN_PATTERN.findall(line):
            if token.lower() not in STOPWORDS and token not in options:
                options.append(token)
        bullet = BULLET_PATTERN.match(line)
        if bullet:
            value = re.sub(r"\s+", "_", bullet.group(1).strip().lower())
            if value not in STOPWORDS and value not in options:
                options.append(value)
        bare = BARE_OPTIONS_PATTERN.search(line)
        if bare:
            for token in re.split(r"\s*,\s*", bare.group(1)):
                value = token.strip().lower()
                if value not in STOPWORDS and value not in options:
                    options.append(value)
    return options[:12]


def candidate_id(path: Path, line_number: int) -> str:
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", str(path.with_suffix(""))).strip("_").lower()
    return f"{stem}_line_{line_number}"


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _call_signal(name: str, source: str, *, raw_context: str | None = None) -> str | None:
    lowered = name.lower()
    leaf_name = name.rsplit(".", 1)[-1]
    provider = MODEL_PROVIDER_PATTERN.search(source)
    suffixes = (
        "chat.completions.create", "chat.completions.parse",
        "responses.create", "responses.parse",
        "messages.create", "messages.stream",
        "model.invoke", "llm.invoke",
        "generate_text", "generatetext", "completion",
    )
    for suffix in suffixes:
        if lowered == suffix or lowered.endswith(f".{suffix}"):
            if suffix == "completion" and leaf_name != "completion":
                continue
            requires_provider = suffix.startswith(("chat.", "responses.", "messages.")) or suffix == "completion"
            if requires_provider and not provider:
                return None
            return suffix
    raw_http_suffixes = (
        "urllib.request.urlopen", "urlopen",
        "requests.post", "requests.request",
        "httpx.post", "httpx.request",
        "fetch", "axios.post", "axios.request",
    )
    raw_provider = MODEL_PROVIDER_PATTERN.search(raw_context if raw_context is not None else source)
    if raw_provider:
        for suffix in raw_http_suffixes:
            if lowered == suffix or lowered.endswith(f".{suffix}"):
                return f"{raw_provider.group(0).lower()}:{suffix}"
    return None


def _python_call_sites(source: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Precision is more important than guessing whether invalid Python is executable.
        return []
    lines = source.splitlines()
    sites: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        index = node.lineno - 1
        context = "\n".join(lines[max(0, index - 8):min(len(lines), index + 9)])
        signal = _call_signal(name, source, raw_context=context) if name else None
        if signal:
            sites.setdefault(index, signal)
    return sorted(sites.items())


def _mask_non_code(lines: list[str]) -> list[str]:
    """Mask strings and comments while preserving positions for call matching."""
    masked: list[str] = []
    block_comment = False
    quote: str | None = None
    for line in lines:
        output = list(line)
        index = 0
        while index < len(line):
            if block_comment:
                output[index] = " "
                if line.startswith("*/", index):
                    if index + 1 < len(output):
                        output[index + 1] = " "
                    block_comment = False
                    index += 2
                else:
                    index += 1
                continue
            if quote:
                output[index] = " "
                if line[index] == "\\":
                    if index + 1 < len(output):
                        output[index + 1] = " "
                    index += 2
                elif line[index] == quote:
                    quote = None
                    index += 1
                else:
                    index += 1
                continue
            if line.startswith("//", index) or line[index] == "#":
                output[index:] = " " * (len(output) - index)
                break
            if line.startswith("/*", index):
                output[index] = " "
                if index + 1 < len(output):
                    output[index + 1] = " "
                block_comment = True
                index += 2
                continue
            if line[index] in {"'", '"', "`"}:
                quote = line[index]
                output[index] = " "
            index += 1
        masked.append("".join(output))
    return masked


def _generic_call_sites(lines: list[str], source: str) -> list[tuple[int, str]]:
    sites: dict[int, str] = {}
    for index, line in enumerate(_mask_non_code(lines)):
        for match in DIRECT_CALL_PATTERN.finditer(line):
            context = "\n".join(lines[max(0, index - 8):min(len(lines), index + 9)])
            signal = _call_signal(match.group("call"), source, raw_context=context)
            if signal:
                sites.setdefault(index, signal)
    return sorted(sites.items())


def _scan_file(path: Path, root: Path) -> list[tuple[dict, str]]:
    if path.suffix.lower() not in CODE_EXTENSIONS:
        return []
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = source.splitlines()
    call_sites = (
        _python_call_sites(source)
        if path.suffix.lower() == ".py"
        else _generic_call_sites(lines, source)
    )
    found: list[tuple[dict, str]] = []
    for index, signal in call_sites:
        start = max(0, index - 8)
        end = min(len(lines), index + 9)
        window = lines[start:end]
        joined = "\n".join(window)
        options = extract_options(window)
        semantic = bool(SEMANTIC_PATTERN.search(joined))
        bounded = len(options) >= 2
        strength = "strong" if bounded and semantic else "medium" if bounded else "weak"
        relative = path.relative_to(root) if root.is_dir() else path.name
        found.append(
            ({
                "candidate_id": candidate_id(relative, index + 1),
                "source_locator": f"{relative}:{index + 1}",
                "llm_signal": signal,
                "input_description": "Context supplied at an executable generative call site; inspect the source at the locator.",
                "observed_options": options,
                "bounded_output": bounded,
                "semantic_interpretation_required": semantic,
                "current_implementation": "generative_call",
                "replacement_strength": strength,
                "review_status": "candidate",
            }, joined)
        )
    return found


def scan_file(path: Path, root: Path) -> list[dict]:
    return [candidate for candidate, _ in _scan_file(path, root)]


def _question_spec() -> dict[str, dict[str, object]]:
    return {
        "decision_shape": {"type": "choice", "instructions": "Using source_window and observed heuristics, classify the decision shape.", "criteria": {"bounded_semantic_decision": "finite options selected using semantic interpretation; excludes open-ended generation", "generative_task": "open-ended content generation; excludes finite option selection", "deterministic_rule": "fixed rule or calculation can decide; excludes semantic judgment", "insufficient_evidence": "the window does not support a reliable classification"}},
        "stable_answer_set": {"type": "noul", "instructions": "Given source_window and observed heuristics, is a stable finite answer set visible?", "criteria": {"true": "a stable finite set is visible", "false": "no stable finite set is visible"}},
        "semantic_interpretation_required": {"type": "noul", "instructions": "Given source_window and observed heuristics, does selecting the answer require semantic interpretation?", "criteria": {"true": "semantic interpretation is required", "false": "a deterministic rule suffices"}},
        "replacement_readiness": {"type": "score", "instructions": "Given source_window and observed heuristics, score readiness for bounded replacement.", "criteria": [description for _, description in READINESS_SPEC]},
    }


def _probability(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"JEV response field {field!r} must be a number from 0 to 1")
    return float(value)


def parse_jev_response(response: object) -> dict[str, object]:
    data = response.get("answers", response) if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise ValueError("JEV response answers must be an object")
    choice = data.get("decision_shape")
    stable_obj, semantic_obj, score_obj = data.get("stable_answer_set"), data.get("semantic_interpretation_required"), data.get("replacement_readiness")
    if not isinstance(choice, dict) or choice.get("type") != "choice" or choice.get("choice") not in DECISION_SHAPES:
        raise ValueError(f"illegal JEV decision_shape choice: {choice!r}")
    probs = choice.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != DECISION_SHAPES or not isinstance(choice.get("confidence"), (int, float)):
        raise ValueError("decision_shape requires probabilities and confidence")
    if abs(sum(_probability(v, "decision_shape.probabilities") for v in probs.values()) - 1) > 1e-6:
        raise ValueError("decision_shape probabilities must sum to 1")
    _probability(choice["confidence"], "decision_shape.confidence")
    if not isinstance(stable_obj, dict) or not isinstance(semantic_obj, dict):
        raise ValueError("JEV Noul answers must be objects")
    stable = _probability(stable_obj.get("noul"), "stable_answer_set.noul")
    semantic = _probability(semantic_obj.get("noul"), "semantic_interpretation_required.noul")
    for obj in (stable_obj, semantic_obj):
        if obj.get("type") != "noul":
            raise ValueError("invalid Noul type")
    if not isinstance(score_obj, dict) or isinstance(score_obj.get("score"), bool) or not isinstance(score_obj.get("score"), (int, float)) or not 0 <= score_obj["score"] <= 2:
        raise ValueError("replacement_readiness.score must be a number from 0 to 2")
    if score_obj.get("type") != "score" or not isinstance(score_obj.get("legend"), dict) or score_obj["legend"] != {str(i): desc for i, (_, desc) in enumerate(READINESS_SPEC)}:
        raise ValueError("replacement_readiness legend is invalid")
    score_probs = score_obj.get("probabilities")
    if not isinstance(score_probs, dict) or set(score_probs) != {"0", "1", "2"} or not isinstance(score_obj.get("confidence"), (int, float)):
        raise ValueError("replacement_readiness requires probabilities and confidence")
    if abs(sum(_probability(v, "replacement_readiness.probabilities") for v in score_probs.values()) - 1) > 1e-6:
        raise ValueError("replacement_readiness probabilities must sum to 1")
    _probability(score_obj["confidence"], "replacement_readiness.confidence")
    readiness_score = float(score_obj["score"])
    readiness = READINESS[min(2, max(0, int(readiness_score + 0.5)))]
    return {"decision_shape": choice["choice"], "stable_answer_set": stable,
            "semantic_interpretation_required": semantic, "replacement_readiness": readiness,
            "readiness_score": readiness_score,
            "priority_score": round((readiness_score / 2) * 0.5 + stable * 0.3 + semantic * 0.2, 6)}


def triage_candidates(candidates: list[dict], windows: list[str], transport: Callable[[dict], object], *, model: str = "jev-latest") -> list[dict]:
    if len(candidates) != len(windows):
        raise ValueError("candidates and windows must have equal lengths")
    ranked: list[tuple[float, str, dict]] = []
    for candidate, window in zip(candidates, windows):
        payload = {"model": model, "state": {"source_locator": candidate["source_locator"], "source_window": window, "heuristics": {key: candidate.get(key) for key in ("observed_options", "bounded_output", "semantic_interpretation_required", "replacement_strength")}}, "questions": _question_spec()}
        raw = transport(payload)
        parsed = parse_jev_response(raw)
        triage = dict(parsed)
        triage["model"] = raw.get("model", model) if isinstance(raw, dict) else model
        if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
            triage["usage"] = raw["usage"]
        candidate["jev_triage"] = triage
        candidate["review_status"] = "candidate"
        ranked.append((float(parsed["priority_score"]), candidate["candidate_id"], candidate))
    ordered = sorted(ranked, key=lambda item: (-item[0], item[1]))
    for rank, (_, _, candidate) in enumerate(ordered, 1):
        candidate["jev_triage"]["rank"] = rank
    return [item[2] for item in ordered]


def _http_transport(endpoint: str, api_key: str, timeout: float) -> Callable[[dict], object]:
    if not endpoint.lower().startswith("https://"):
        raise ValueError("JEV endpoint must use HTTPS")
    def send(payload: dict) -> object:
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, ValueError) as exc:
            raise RuntimeError(f"JEV request failed: {exc}") from exc
    return send


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--jev-triage", action="store_true")
    parser.add_argument("--jev-model", default="jev-latest")
    parser.add_argument("--jev-endpoint", default=DEFAULT_JEV_ENDPOINT)
    parser.add_argument("--jev-timeout", type=float, default=30.0)
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"source does not exist: {args.source}")
    root = args.source if args.source.is_dir() else args.source.parent
    candidates = []
    windows: list[str] = []
    output_path = args.output.resolve() if args.output else None
    for path in iter_files(args.source):
        if output_path and path.resolve() == output_path:
            continue
        scanned = _scan_file(path, root)
        candidates.extend(candidate for candidate, _ in scanned)
        windows.extend(window for _, window in scanned)
    if args.jev_triage:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise SystemExit("--jev-triage requires TYPESAFE_API_KEY")
        candidates = triage_candidates(candidates, windows, _http_transport(args.jev_endpoint, api_key, args.jev_timeout), model=args.jev_model)
    document = {
        "schema_version": "1.0",
        "discovery_mode": "heuristic_static_scan",
        "source_root": str(args.source),
        "review_required": True,
        "surface_candidates": candidates,
    }
    rendered = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {len(candidates)} candidate(s) to {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
