"""Minimal predicate language shared by the bundle and the generated adapter.

Grammar (formalizes the convention already used in ``observable_predicate``,
``preconditions[]`` and ``guard[]``):

    predicate := clause ( 'and' clause )*
    clause    := IDENT OP value
    OP        := '==' | '!=' | '>=' | '<=' | '>' | '<'
    value     := NUMBER | true | false | null | 'string' | "string" | IDENT

A bare identifier on the right-hand side resolves to ``state[ident]`` when that
key exists and to the literal string otherwise. A missing left-hand key makes
the clause false: unknown is never satisfied. This file is embedded verbatim
into generated adapters, so it must stay standard-library only.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_OPS = ("==", "!=", ">=", "<=", ">", "<")
_CLAUSE = re.compile(
    r"^\s*(?P<lhs>[A-Za-z_][A-Za-z0-9_.]*)\s*(?P<op>==|!=|>=|<=|>|<)\s*(?P<rhs>.+?)\s*$"
)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class PredicateError(ValueError):
    """Raised when a predicate string does not follow the grammar."""


def _literal(text: str) -> tuple[str, Any]:
    """Return ("literal", value) or ("ident", name)."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return "literal", text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return "literal", True
    if lowered == "false":
        return "literal", False
    if lowered in {"null", "none"}:
        return "literal", None
    try:
        return "literal", float(text) if any(c in text for c in ".eE") else int(text)
    except ValueError:
        pass
    if _IDENT.match(text):
        return "ident", text
    raise PredicateError(f"unrecognised value {text!r}")


def parse_predicate(text: str) -> list[tuple[str, str, tuple[str, Any]]]:
    """Parse ``text`` into ``[(lhs, op, (kind, value)), ...]``; raise PredicateError."""
    if not isinstance(text, str) or not text.strip():
        raise PredicateError("empty predicate")
    clauses = []
    for part in re.split(r"\s+and\s+", text.strip()):
        match = _CLAUSE.match(part)
        if not match:
            raise PredicateError(f"malformed clause {part!r} in {text!r}")
        clauses.append((match.group("lhs"), match.group("op"), _literal(match.group("rhs"))))
    return clauses


_MISSING = object()


def _lookup(state: Mapping[str, Any], path: str) -> Any:
    value: Any = state
    for key in path.split("."):
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        else:
            return _MISSING
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare(left: Any, op: str, right: Any) -> bool:
    ln, rn = _number(left), _number(right)
    if ln is not None and rn is not None:
        left, right = ln, rn
    elif op in ("<", "<=", ">", ">="):
        return False
    elif isinstance(left, bool) or isinstance(right, bool):
        pass
    else:
        left, right = (None if left is None else str(left)), (None if right is None else str(right))
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


def evaluate_predicate(text: str, state: Mapping[str, Any]) -> bool:
    """True when every clause of ``text`` holds for ``state``."""
    for lhs, op, (kind, value) in parse_predicate(text):
        left = _lookup(state, lhs)
        if left is _MISSING:
            return False
        if kind == "ident":
            resolved = _lookup(state, value)
            value = value if resolved is _MISSING else resolved
        if not _compare(left, op, value):
            return False
    return True


def evaluate_all(predicates: Iterable[str], state: Mapping[str, Any]) -> list[str]:
    """Return the predicates that do not hold (empty list means all pass)."""
    return [text for text in predicates if not evaluate_predicate(text, state)]
