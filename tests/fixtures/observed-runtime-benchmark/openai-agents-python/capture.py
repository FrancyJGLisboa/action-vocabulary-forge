#!/usr/bin/env python3
"""Capture bounded handoff decisions from an installed OpenAI Agents SDK checkout.

The model is the SDK's deterministic ScriptedModel. No provider, API key, or
network request is used. Only public lifecycle-hook arguments are recorded;
model inputs, outputs, and private reasoning are deliberately excluded.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents import Agent, Runner
from agents.lifecycle import RunHooks
from agents.testing import ScriptedModel, assistant_message, function_call


OPPORTUNITY_ID = "agent_handoff_route"
ROUTES = {
    "triage": ["refund", "knowledge"],
    "refund": ["closer", "human"],
}


class HandoffObserver(RunHooks):
    """Translate actual SDK handoff callbacks into the Forge event contract."""

    def __init__(self, case_id: str, case_index: int) -> None:
        self.case_id = case_id
        self.case_index = case_index
        self.events: list[dict[str, Any]] = []

    async def on_handoff(self, context: Any, from_agent: Agent, to_agent: Agent) -> None:
        del context
        step = len(self.events) + 1
        occurred_at = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc) + timedelta(
            minutes=self.case_index,
            seconds=step,
        )
        available = ROUTES[from_agent.name]
        selected = to_agent.name
        if selected not in available:
            raise RuntimeError(f"runtime emitted unsupported handoff {from_agent.name}->{selected}")
        self.events.append(
            {
                "event_id": f"openai-{self.case_id}-{step}",
                "case_id": self.case_id,
                "opportunity_id": OPPORTUNITY_ID,
                "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
                "actor_type": "agent",
                "state_before": {
                    "active_agent": from_agent.name,
                    "handoff_index": step,
                },
                "available_actions": available,
                "selected_action": selected,
                "state_after": {"active_agent": selected},
                "source_ref": "openai-agents-python:RunHooks.on_handoff",
            }
        )


async def run_case(case_id: str, case_index: int) -> list[dict[str, Any]]:
    closer_model = ScriptedModel([[assistant_message("case closed")]])
    human_model = ScriptedModel([[assistant_message("human review requested")]])
    knowledge_model = ScriptedModel([[assistant_message("knowledge answer")]])
    refund_model = ScriptedModel(
        [[function_call("transfer_to_closer", {}, call_id=f"{case_id}-closer")]]
    )
    triage_model = ScriptedModel(
        [[function_call("transfer_to_refund", {}, call_id=f"{case_id}-refund")]]
    )

    closer = Agent(name="closer", model=closer_model)
    human = Agent(name="human", model=human_model)
    knowledge = Agent(name="knowledge", model=knowledge_model)
    refund = Agent(name="refund", model=refund_model, handoffs=[closer, human])
    triage = Agent(name="triage", model=triage_model, handoffs=[refund, knowledge])
    hooks = HandoffObserver(case_id, case_index)

    result = await Runner.run(triage, input=f"synthetic benchmark case {case_id}", hooks=hooks)
    if result.last_agent.name != "closer" or len(hooks.events) != 2:
        raise RuntimeError("scripted runtime did not traverse the expected two-handoff path")
    return hooks.events


async def capture() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, case_id in enumerate(("handoff-case-a", "handoff-case-b"), 1):
        events.extend(await run_case(case_id, index))
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    events = asyncio.run(capture())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    print(f"captured {len(events)} handoff events across 2 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
