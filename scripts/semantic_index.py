#!/usr/bin/env python3
"""Build or query a SQLite full-text index for a Semantic Decision Bundle."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

import yaml


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def records(bundle: Path) -> Iterable[tuple[str, str, str, str, str]]:
    manifest = _yaml(bundle / "material_manifest.yaml")
    for item in manifest.get("sources", []):
        yield "source", item["source_id"], item.get("locator", item["source_id"]), _text(item), _text(item)

    ir = _yaml(bundle / "semantic_ir.yaml")
    for item in ir.get("concepts", []):
        yield "concept", item["concept_id"], item.get("description", item["concept_id"]), _text(item.get("description")), _text(item)
    for item in ir.get("relations", []):
        yield "relation", item["relation_id"], item.get("predicate", item["relation_id"]), _text(item), _text(item)

    registry = _yaml(bundle / "judgment_registry.yaml")
    for item in registry.get("families", []):
        text = " ".join(_text(item.get(key)) for key in ("instructions_template", "purpose"))
        yield "family", item["family_id"], item["family_id"], text, _text(item)
    for item in registry.get("judgments", []):
        text = " ".join(_text(item.get(key)) for key in ("instructions", "criteria", "purpose", "state_paths"))
        yield "judgment", item["judgment_id"], item["judgment_id"], text, _text(item)

    action_doc = _yaml(bundle / "action_registry.yaml")
    for item in action_doc.get("actions", []):
        text = " ".join(_text(item.get(key)) for key in ("description", "choose_when", "do_not_choose_when", "aliases"))
        yield "action", item["action_id"], item.get("description", item["action_id"]), text, _text(item)
    state_doc = _yaml(bundle / "state_registry.yaml")
    for item in state_doc.get("states", []):
        yield "state", item["state_id"], item.get("description", item["state_id"]), _text(item), _text(item)
    surface_doc = _yaml(bundle / "decision_surfaces.yaml")
    for item in surface_doc.get("decision_surfaces", []):
        yield "surface", item["surface_id"], item.get("description", item["surface_id"]), _text(item), _text(item)

    evidence_path = bundle / "evidence_ledger.jsonl"
    for line in evidence_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        yield "evidence", item["evidence_id"], item.get("claim", item["evidence_id"]), _text(item), _text(item)


def build(bundle: Path, database: Path) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    database.unlink(missing_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE vocabulary USING fts5(kind UNINDEXED, item_id UNINDEXED, title, body, metadata UNINDEXED)"
        )
        rows = list(records(bundle))
        connection.executemany(
            "INSERT INTO vocabulary(kind, item_id, title, body, metadata) VALUES (?, ?, ?, ?, ?)", rows
        )
        connection.commit()
    return len(rows)


def search(database: Path, query: str, *, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
    terms = re.findall(r"[\w.-]+", query, flags=re.UNICODE)
    terms = [term for term in terms if term.upper() not in {"AND", "OR", "NOT", "NEAR"}]
    if not terms:
        raise ValueError("search query must contain at least one word")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    # Treat user input as words, not raw FTS5 syntax. This avoids malformed or
    # operator-heavy queries changing the meaning of the generated statement.
    safe_query = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
    where = "vocabulary MATCH ?"
    params: list[Any] = [safe_query]
    if kind:
        where += " AND kind = ?"
        params.append(kind)
    params.append(limit)
    sql = (
        "SELECT kind, item_id, title, snippet(vocabulary, 3, '[', ']', ' … ', 18), bm25(vocabulary) "
        f"FROM vocabulary WHERE {where} ORDER BY bm25(vocabulary) LIMIT ?"
    )
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(sql, params).fetchall()
    return [
        {"kind": row[0], "id": row[1], "title": row[2], "snippet": row[3], "rank": row[4]}
        for row in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("bundle", type=Path)
    build_parser.add_argument("database", type=Path)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("database", type=Path)
    search_parser.add_argument("query")
    search_parser.add_argument("--kind")
    search_parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.command == "build":
        count = build(args.bundle, args.database)
        print(f"indexed {count} semantic records in {args.database}")
        return 0
    print(json.dumps(search(args.database, args.query, limit=args.limit, kind=args.kind), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
