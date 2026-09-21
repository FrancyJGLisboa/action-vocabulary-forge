"""Shared helpers: render a bundle into an importable module."""

from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_adapter  # noqa: E402

EXAMPLE = ROOT / "examples" / "validation-bundle"


def load_example() -> dict:
    return generate_adapter.load_bundle(EXAMPLE)


def import_bundle(bundle: dict) -> types.ModuleType:
    """Render the bundle and import it as a uniquely named module."""
    name = f"generated_{uuid.uuid4().hex}"
    source = generate_adapter.render(bundle, name)
    directory = Path(tempfile.mkdtemp())
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def choice_response(question_id: str, choice: str, confidence: float, **extra) -> dict:
    return {
        "answers": {question_id: {"choice": choice, "confidence": confidence, "probabilities": {choice: confidence}}},
        "model": "jev-test",
        "usage": {"input_tokens": 10, "output_tokens": 1},
        **extra,
    }


def clone(bundle: dict) -> dict:
    return copy.deepcopy(bundle)


GOOD_STATE = {"state_id": "validation_failed", "record_id": "r-1", "retry_budget": 1, "record_exists": True, "unresolved_failure": True}
