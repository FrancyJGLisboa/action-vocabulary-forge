"""Controlled adapter derived from the pinned public inactive-issue workflow fixture.

This harness is intentionally not imported or executed by the Forge. The
benchmark host calls it and emits privacy-minimized binding observations.
"""

from __future__ import annotations


def mark_stale(state: dict) -> dict:
    """Add the public workflow's stale label after seven inactive days."""
    if int(state.get("inactive_days", 0)) < 7:
        raise ValueError("issue is not yet eligible for the stale label")
    labels = list(state.get("labels", []))
    if "stale" not in labels:
        labels.append("stale")
    return {**state, "labels": labels}
