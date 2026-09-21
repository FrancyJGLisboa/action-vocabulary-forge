#!/usr/bin/env python3
"""Run one compiled Decision Surface through TypeSafe JEV and validate its answer."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"JSON file {path} must contain an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiled_surface", type=Path, help="JSON from compile_jev_surface.py")
    parser.add_argument("--context", required=True, help="context the JEV judgment should interpret")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument("--endpoint", default=os.environ.get("TYPESAFE_API_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise SystemExit("TYPESAFE_API_KEY is not set")
    compiled = load_json(args.compiled_surface)
    choices = compiled.get("choices", [])
    if not isinstance(choices, list) or len(choices) < 2:
        raise SystemExit("compiled surface must contain at least two choices")

    criteria: dict[str, Any] = {}
    for choice in choices:
        choice_id = choice.get("id")
        if not isinstance(choice_id, str) or not choice_id:
            raise SystemExit("compiled choice is missing id")
        criteria[choice_id] = choice.get("criterion") or choice.get("description") or choice_id

    surface_id = compiled.get("surface_id", "decision_surface")
    question_id = f"{surface_id}_choice"
    payload = {
        "model": args.model,
        "state": {
            "context": args.context,
            "observable_state": compiled.get("state_id"),
            "decision_surface": surface_id,
            "allowed_actions": [choice["id"] for choice in choices],
        },
        "questions": {
            question_id: {
                "type": "choice",
                "instructions": "Choose exactly one allowed action whose criterion best matches the context.",
                "criteria": criteria,
            }
        },
    }
    request = urllib.request.Request(
        args.endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"JEV HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"JEV request failed: {exc.reason}") from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JEV returned invalid JSON (HTTP {status})") from exc
    answer = result.get("answers", {}).get(question_id)
    if not isinstance(answer, dict):
        raise SystemExit(f"JEV response omitted answer {question_id!r}")
    selected = answer.get("choice")
    allowed = set(criteria)
    if selected not in allowed:
        raise SystemExit(f"JEV returned illegal choice {selected!r}; allowed={sorted(allowed)}")

    output = {
        "surface_id": surface_id,
        "state_id": compiled.get("state_id"),
        "selected_action": selected,
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities", {}),
        "model": result.get("model", args.model),
        "usage": result.get("usage", {}),
        "fallback_action": compiled.get("fallback_action"),
        "abstention_choice": compiled.get("abstention_choice"),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
