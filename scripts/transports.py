"""Local-model transports with the same answer shape as TypeSafe System One.

A transport is ``callable(payload) -> response`` where ``payload`` is what
``build_payload`` produces (``{"model", "state": {"context", "system_id"},
"questions": {qid: {"type", "instructions", "criteria"}}}``) and ``response`` is
``{"answers": {qid: {"choice"|"noul"|"score", "confidence", "probabilities"}},
"model", "usage"}``. The generated adapter picks one by ``provider`` when the
host injects none. This file is embedded verbatim into generated adapters, so
it must stay standard-library only; optional SDKs are imported inside the
transport at call time.

Laya (convaiinnovations/laya, Apache-2.0) exposes the same primitives in
process: ``laya.load(repo, subfolder=ckpt).predict(state, questions)``. Its
context is 512-1024 tokens, so the state is compacted and one question is sent
per call. ``pip install laya "numpy<2"``.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Mapping

_LAYA_LOCK = threading.Lock()
_LAYA_AGENTS: dict[str, Any] = {}
LAYA_REPO = "convaiinnovations/laya"
LAYA_CHECKPOINTS = {"typed-decisions", "multilingual"}


def _compact(context: Any, max_chars: int) -> Any:
    """Shrink the context so it fits a small local model; strings are truncated, mappings pruned."""
    if isinstance(context, str):
        return context[:max_chars]
    text = json.dumps(context, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return context
    if isinstance(context, Mapping):
        out: dict[str, Any] = {}
        budget = max_chars
        for key, value in context.items():
            piece = json.dumps(value, ensure_ascii=False, default=str)
            if len(piece) > budget:
                piece = piece[: max(0, budget)]
                out[key] = piece
                break
            out[key] = value
            budget -= len(piece) + len(str(key)) + 4
            if budget <= 0:
                break
        return out
    return text[:max_chars]


def _load_laya(checkpoint: str) -> Any:
    with _LAYA_LOCK:
        agent = _LAYA_AGENTS.get(checkpoint)
        if agent is None:
            try:
                import laya  # type: ignore
            except ImportError as exc:
                raise RuntimeError('laya is not installed: pip install laya "numpy<2", or pass transport=') from exc
            agent = laya.load(LAYA_REPO, subfolder=checkpoint if checkpoint in LAYA_CHECKPOINTS else None)
            _LAYA_AGENTS[checkpoint] = agent
    return agent


def laya_transport(model: str = "laya:typed-decisions", max_state_chars: int = 2000) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """One in-process Laya ``predict`` per question, answers merged into the JEV response shape."""
    checkpoint = model.split(":", 1)[1] if ":" in model else "typed-decisions"

    def send(payload: dict[str, Any]) -> dict[str, Any]:
        agent = _load_laya(checkpoint)
        state = dict(payload.get("state") or {})
        state["context"] = _compact(state.get("context"), max_state_chars)
        answers: dict[str, Any] = {}
        chars = 0
        for qid, question in (payload.get("questions") or {}).items():
            with _LAYA_LOCK:
                result = agent.predict(state, {qid: question})
            answer = (result.get("answers") or {}).get(qid)
            if not isinstance(answer, Mapping):
                raise RuntimeError(f"laya returned no answer for {qid}")
            answers[qid] = dict(answer)
            chars += len(json.dumps(state, default=str)) + len(json.dumps(question, default=str))
        return {"answers": answers, "model": f"laya:{checkpoint}", "usage": {"input_chars": chars}}

    return send


PROVIDERS: dict[str, Callable[..., Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "laya": laya_transport,
}
