#!/usr/bin/env python3
"""Acquire bounded evidence snapshots for work-system reconstruction.

This module performs transport, inventory, classification, and hashing only. It
does not infer a workflow. System 2 receives the resulting evidence package and
the existing reconstruction compiler validates every proposed semantic claim.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterable, Iterator

import yaml


SUPPORTED_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".csv",
    ".dot",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".kt",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".rst",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}
IGNORED_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
DEFAULT_MAX_FILES = 120
DEFAULT_MAX_SOURCE_BYTES = 1_000_000


class AcquisitionError(ValueError):
    """An input cannot be acquired within the evidence safety contract."""


@dataclass(frozen=True)
class RepositorySnapshot:
    root: Path
    repository: str
    commit: str


UrlFetcher = Callable[[str, int], tuple[bytes, str]]
RepositoryMaterializer = Callable[[str], ContextManager[RepositorySnapshot]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_https_url(value: str, *, label: str = "URL") -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AcquisitionError(f"{label} must be an HTTPS URL without embedded credentials: {value}")
    if parsed.fragment:
        raise AcquisitionError(f"{label} must not contain a fragment: {value}")
    return urllib.parse.urlunsplit(parsed)


def normalize_github_repository(value: str) -> str:
    normalized = _validate_https_url(value, label="repository")
    parsed = urllib.parse.urlsplit(normalized)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.hostname != "github.com" or len(parts) != 2 or parsed.query:
        raise AcquisitionError(
            "repository must be a GitHub HTTPS repository URL such as https://github.com/owner/repo"
        )
    repository = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    if not repository:
        raise AcquisitionError("repository must name a GitHub repository")
    return f"https://github.com/{parts[0]}/{repository}"


def _default_fetch_url(url: str, max_bytes: int) -> tuple[bytes, str]:
    parsed = urllib.parse.urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    fetch_url = url
    if parsed.hostname == "github.com" and len(parts) >= 5 and parts[2] == "blob":
        fetch_url = urllib.parse.urlunsplit(
            ("https", "raw.githubusercontent.com", "/" + "/".join(parts[:2] + parts[3:]), "", "")
        )
    request = urllib.request.Request(
        fetch_url,
        headers={"User-Agent": "decision-system-forge/0.5 evidence-acquisition"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            _validate_https_url(final_url, label="redirect target")
            content_type = response.headers.get_content_type()
            content = response.read(max_bytes + 1)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise AcquisitionError(f"cannot fetch URL {url}: {exc}") from exc
    if len(content) > max_bytes:
        raise AcquisitionError(f"URL exceeds the {max_bytes}-byte evidence limit: {url}")
    allowed = content_type.startswith("text/") or content_type in {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
    }
    if not allowed:
        raise AcquisitionError(f"URL did not return supported textual content ({content_type}): {url}")
    return content, content_type


@contextlib.contextmanager
def materialize_github_repository(url: str) -> Iterator[RepositorySnapshot]:
    repository = normalize_github_repository(url)
    with tempfile.TemporaryDirectory(prefix="forge-repository-") as directory:
        root = Path(directory) / "repository"
        command = [
            "git",
            "clone",
            "--depth=1",
            "--filter=blob:none",
            "--single-branch",
            "--no-checkout",
            repository,
            str(root),
        ]
        try:
            clone = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AcquisitionError(f"cannot acquire repository {repository}: {exc}") from exc
        if clone.returncode != 0:
            detail = (clone.stderr or clone.stdout).strip().splitlines()
            message = detail[-1] if detail else "git clone failed"
            raise AcquisitionError(f"cannot acquire repository {repository}: {message}")
        commit_process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        commit = commit_process.stdout.strip().lower()
        if commit_process.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise AcquisitionError(f"cannot resolve a full commit for repository {repository}")
        tree_process = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if tree_process.returncode != 0:
            raise AcquisitionError(f"cannot inventory repository tree for {repository}")
        tree_paths: list[Path] = []
        for value in tree_process.stdout.splitlines():
            path = Path(value)
            if any(part in IGNORED_PARTS for part in path.parts):
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS and path.name.lower() not in {
                "jenkinsfile",
                "makefile",
            }:
                continue
            tree_paths.append(path)
        selected = [path.as_posix() for path in _select_diverse(tree_paths, DEFAULT_MAX_FILES)]
        if selected:
            checkout_process = subprocess.run(
                ["git", "checkout", "HEAD", "--", *selected],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if checkout_process.returncode != 0:
                detail = (checkout_process.stderr or checkout_process.stdout).strip().splitlines()
                message = detail[-1] if detail else "selective checkout failed"
                raise AcquisitionError(f"cannot checkout evidence from {repository}: {message}")
        yield RepositorySnapshot(root=root, repository=repository, commit=commit)


def _source_kind(path_hint: str) -> str:
    lowered = path_hint.lower().replace("\\", "/")
    name = Path(lowered).name
    suffix = Path(name).suffix
    if "/.github/workflows/" in f"/{lowered}" or name in {"jenkinsfile", "makefile"}:
        return "deterministic_workflow"
    if "pull_request_template" in lowered or "issue_template" in lowered:
        return "interface_template"
    if name in {"agents.md", "claude.md", "skill.md"} or "/skills/" in f"/{lowered}":
        return "agent_instruction"
    if suffix in {".md", ".rst", ".txt"} and any(
        token in name for token in ("contributing", "policy", "security", "governance", "sop", "runbook")
    ):
        return "policy_document"
    if suffix in {".jsonl", ".csv"} and any(
        token in name for token in ("event", "history", "log", "trace", "resolved", "case")
    ):
        return "work_history"
    if suffix in CODE_EXTENSIONS:
        return "code"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".xml", ".csv", ".jsonl"}:
        return "schema_or_data"
    return "document"


def _evidence_status(kind: str) -> str:
    return "observed" if kind == "work_history" else "declared"


def _priority(path: Path) -> tuple[int, str]:
    rendered = path.as_posix().lower()
    name = path.name.lower()
    score = 0
    if path.suffix.lower() in {".md", ".rst", ".txt"} and any(
        token in name for token in ("contributing", "policy", "governance", "sop", "runbook")
    ):
        score += 100
    if name in {"agents.md", "claude.md", "skill.md"}:
        score += 95
    if "/.github/workflows/" in f"/{rendered}":
        score += 90
    if "pull_request_template" in rendered or "issue_template" in rendered:
        score += 85
    if any(token in name for token in ("history", "events", "traces", "resolved_cases")):
        score += 80
    if name.startswith("readme"):
        score += 70
    if path.suffix.lower() in CODE_EXTENSIONS:
        score += 30
    return (-score, rendered)


def _select_diverse(candidates: Iterable[Path], limit: int) -> list[Path]:
    ordered = sorted(candidates, key=_priority)
    selected: list[Path] = []
    selected_set: set[Path] = set()
    seen_kinds: set[str] = set()
    for candidate in ordered:
        kind = _source_kind(candidate.as_posix())
        if kind in seen_kinds:
            continue
        selected.append(candidate)
        selected_set.add(candidate)
        seen_kinds.add(kind)
        if len(selected) >= limit:
            return selected
    for candidate in ordered:
        if candidate in selected_set:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _candidate_files(root: Path, *, max_files: int) -> list[Path]:
    candidates: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS and candidate.name.lower() not in {
            "jenkinsfile",
            "makefile",
        }:
            continue
        candidates.append(candidate)
    return _select_diverse(candidates, max_files)


def _read_candidate(path: Path, *, max_bytes: int) -> tuple[bytes | None, str | None]:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return None, "oversized"
        content = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if b"\x00" in content:
        return None, "binary"
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return None, "non_utf8"
    return content, None


def _url_suffix(url: str) -> str:
    suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    return suffix if suffix in SUPPORTED_EXTENSIONS else ".txt"


def _render_request(system_id: str, sources: list[dict[str, Any]]) -> str:
    lines = [
        "# System 2 reconstruction request",
        "",
        f"System: `{system_id}`",
        "",
        "The deterministic acquisition phase collected and hashed the evidence below. System 2 must now infer only evidence-supported actors, artifacts, activities, states, decisions, outcomes, links, and workflows.",
        "",
        "## Evidence",
        "",
        "| Source ID | Kind | Status | Snapshot | Origin |",
        "| --- | --- | --- | --- | --- |",
    ]
    for source in sources:
        lines.append(
            f"| `{source['source_id']}` | `{source['kind']}` | `{source['evidence_status']}` | "
            f"`{source['file']}` | {source['origin']['locator']} |"
        )
    lines.extend(
        [
            "",
            "## Required method",
            "",
            "1. Read every snapshot before proposing a workflow.",
            "2. Put exact quotes from snapshots in every node and link evidence entry.",
            "3. Keep declared, observed, inferred, unknown, and conflicting claims distinct.",
            "4. Give each decision two to twelve bounded candidate actions only when supported.",
            "5. Name evidence gaps explicitly. Do not silently complete missing workflow steps.",
            "6. Use `repeated_case_count: 0` unless observed cases support a larger count.",
            "7. Edit `work_system_proposal.yaml`; do not edit hashes or the evidence manifest.",
            "8. Do not include private chain-of-thought. The proposal is a concise evidence model.",
            "",
            "The proposal is descriptive. It does not authorize actions, create executable bindings, or make any candidate eligible for production or JEV shadow use.",
            "",
        ]
    )
    return "\n".join(lines)


def _proposal_template(system_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "system_id": system_id,
        "nodes": [],
        "links": [],
        "workflows": [],
    }


def acquire(
    *,
    project: Path,
    system_id: str,
    repositories: Iterable[str] = (),
    urls: Iterable[str] = (),
    sources: Iterable[str | Path] = (),
    max_files: int = DEFAULT_MAX_FILES,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    url_fetcher: UrlFetcher | None = None,
    repository_materializer: RepositoryMaterializer | None = None,
) -> dict[str, Any]:
    """Acquire inputs into ``project`` and return the generated contracts."""
    if not isinstance(system_id, str) or not system_id.strip():
        raise AcquisitionError("system_id must be a non-empty string")
    if max_files < 1 or max_files > 2_000:
        raise AcquisitionError("max_files must be between 1 and 2000")
    if max_source_bytes < 1 or max_source_bytes > 20_000_000:
        raise AcquisitionError("max_source_bytes must be between 1 and 20000000")

    repositories = list(repositories)
    urls = list(urls)
    local_sources = [Path(value).expanduser() for value in sources]
    if not repositories and not urls and not local_sources:
        raise AcquisitionError("provide at least one --repo, --url, or --source input")

    project.mkdir(parents=True, exist_ok=True)
    snapshot_dir = project / "evidence" / "sources"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    fetch = url_fetcher or _default_fetch_url
    materialize = repository_materializer or materialize_github_repository
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen_origins: set[str] = set()
    seen_digests: set[str] = set()
    repository_provenance: list[dict[str, str]] = []

    def add_content(*, content: bytes, path_hint: str, origin: dict[str, str]) -> None:
        locator = origin["locator"]
        if locator in seen_origins or _sha256_bytes(content) in seen_digests:
            skipped.append({"locator": locator, "reason": "duplicate"})
            return
        if len(included) >= max_files:
            skipped.append({"locator": locator, "reason": "file_limit"})
            return
        if len(content) > max_source_bytes:
            skipped.append({"locator": locator, "reason": "oversized"})
            return
        if b"\x00" in content:
            skipped.append({"locator": locator, "reason": "binary"})
            return
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"locator": locator, "reason": "non_utf8"})
            return
        digest = _sha256_bytes(content)
        identity = hashlib.sha256(f"{locator}\0{digest}".encode("utf-8")).hexdigest()[:16]
        source_id = f"source_{identity}"
        suffix = Path(path_hint).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            suffix = ".txt"
        relative = Path("evidence") / "sources" / f"{source_id}{suffix}"
        destination = project / relative
        destination.write_bytes(content)
        kind = _source_kind(path_hint)
        status = _evidence_status(kind)
        entry: dict[str, Any] = {
            "source_id": source_id,
            "kind": kind,
            "evidence_status": status,
            "file": relative.as_posix(),
            "sha256": digest,
            "bytes": len(content),
            "origin": origin,
        }
        if status == "observed":
            entry["captured_at"] = _now()
        included.append(entry)
        seen_origins.add(locator)
        seen_digests.add(digest)

    for supplied in local_sources:
        if supplied.is_symlink():
            raise AcquisitionError(f"local source must not be a symlink: {supplied}")
        path = supplied.resolve()
        if not path.exists():
            raise AcquisitionError(f"local source does not exist: {supplied}")
        candidates = [path] if path.is_file() else _candidate_files(path, max_files=max_files)
        for candidate in candidates:
            locator = str(candidate.resolve())
            if locator in seen_origins:
                skipped.append({"locator": locator, "reason": "duplicate"})
                continue
            content, reason = _read_candidate(candidate, max_bytes=max_source_bytes)
            if reason:
                skipped.append({"locator": locator, "reason": reason})
                seen_origins.add(locator)
                continue
            assert content is not None
            add_content(
                content=content,
                path_hint=candidate.as_posix(),
                origin={"type": "local", "locator": locator},
            )

    for supplied in repositories:
        repository = normalize_github_repository(supplied)
        with materialize(repository) as snapshot:
            if snapshot.repository != repository:
                raise AcquisitionError("repository materializer returned a different repository identity")
            if not re.fullmatch(r"[0-9a-f]{40}", snapshot.commit):
                raise AcquisitionError("repository materializer did not return a full lowercase commit")
            repository_provenance.append({"repository": repository, "commit": snapshot.commit})
            for candidate in _candidate_files(snapshot.root, max_files=max_files):
                relative = candidate.resolve().relative_to(snapshot.root.resolve()).as_posix()
                locator = f"{repository}/blob/{snapshot.commit}/{relative}"
                content, reason = _read_candidate(candidate, max_bytes=max_source_bytes)
                if reason:
                    skipped.append({"locator": locator, "reason": reason})
                    continue
                assert content is not None
                add_content(
                    content=content,
                    path_hint=relative,
                    origin={
                        "type": "repository",
                        "locator": locator,
                        "repository": repository,
                        "commit": snapshot.commit,
                        "path": relative,
                    },
                )

    for supplied in urls:
        url = _validate_https_url(supplied)
        if url in seen_origins:
            skipped.append({"locator": url, "reason": "duplicate"})
            continue
        content, _content_type = fetch(url, max_source_bytes)
        add_content(
            content=content,
            path_hint=urllib.parse.urlsplit(url).path or f"source{_url_suffix(url)}",
            origin={"type": "url", "locator": url},
        )

    if not included:
        raise AcquisitionError("no supported textual evidence was found in the supplied inputs")

    included.sort(key=lambda item: (item["kind"], item["origin"]["locator"], item["source_id"]))
    proposal_path = project / "work_system_proposal.yaml"
    proposal_path.write_text(
        yaml.safe_dump(_proposal_template(system_id), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    single_repository = repository_provenance[0] if len(repository_provenance) == 1 else None
    system = {
        "system_id": system_id,
        "provenance_mode": "acquired",
        "repository": single_repository["repository"] if single_repository else None,
        "commit": single_repository["commit"] if single_repository else None,
        "license": "unknown",
        "proposal_file": "work_system_proposal.yaml",
        "proposal_sha256": _sha256_bytes(proposal_path.read_bytes()),
        "sources": included,
    }
    evidence_manifest = {
        "schema_version": "1.0",
        "acquisition_mode": "direct",
        "acquired_at": _now(),
        "systems": [system],
    }
    catalog = {
        "schema_version": "1.0",
        "system_id": system_id,
        "included_count": len(included),
        "skipped_count": len(skipped),
        "limits": {"max_files": max_files, "max_source_bytes": max_source_bytes},
        "repositories": repository_provenance,
        "sources": included,
        "skipped": skipped,
    }
    (project / "evidence_manifest.yaml").write_text(
        yaml.safe_dump(evidence_manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (project / "evidence" / "source_catalog.yaml").write_text(
        yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (project / "reconstruction_request.md").write_text(
        _render_request(system_id, included),
        encoding="utf-8",
    )
    return {
        "evidence_manifest": evidence_manifest,
        "catalog": catalog,
        "request_file": "reconstruction_request.md",
        "proposal_file": "work_system_proposal.yaml",
    }


__all__ = [
    "AcquisitionError",
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_SOURCE_BYTES",
    "RepositorySnapshot",
    "acquire",
    "materialize_github_repository",
    "normalize_github_repository",
]
