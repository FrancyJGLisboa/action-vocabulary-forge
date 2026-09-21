#!/usr/bin/env python3
"""Find code locations that may contain bounded decisions implemented by generative calls."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import yaml


EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".rb", ".php", ".md", ".yaml", ".yml", ".json",
}
SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__"}
LLM_PATTERN = re.compile(
    r"api[.]typesafe[.]ai|systemone|typesafe|anthropic|claude|"
    r"chat[.]completions|responses[.]create|"
    r"\bllm\b|model[.]invoke|generate[_ -]?text|completion",
    re.IGNORECASE,
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


def iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
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


def scan_file(path: Path, root: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[dict] = []
    for index, line in enumerate(lines):
        match = LLM_PATTERN.search(line)
        if not match:
            continue
        if re.match(r"^\s*(from|import)\b", line):
            continue
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
            {
                "candidate_id": candidate_id(relative, index + 1),
                "source_locator": f"{relative}:{index + 1}",
                "llm_signal": match.group(0).lower(),
                "input_description": "Complex context supplied to a generative call; inspect the source at the locator.",
                "observed_options": options,
                "bounded_output": bounded,
                "semantic_interpretation_required": semantic,
                "current_implementation": "generative_call",
                "replacement_strength": strength,
                "review_status": "candidate",
            }
        )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"source does not exist: {args.source}")
    root = args.source if args.source.is_dir() else args.source.parent
    candidates = []
    output_path = args.output.resolve() if args.output else None
    for path in iter_files(args.source):
        if output_path and path.resolve() == output_path:
            continue
        candidates.extend(scan_file(path, root))
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
