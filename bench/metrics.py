"""Per-run metrics computed from the trace, alongside whatever the scenario's scorer returned.

fired_before_action: the expected skill was suggested before the first tool call it was meant to shape.
A suggestion that lands after the work is done is a miss, even when the work happens to be right.
noise: suggestions of other skills per tool call; quiet is part of being useful.
"""
from __future__ import annotations

import re

from bench.scenario import Action, Scenario
from bench.stream import RunTrace, suggested_skills


def _target(inp: dict) -> str:
    raw = inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or inp.get("command") or ""
    return str(raw).replace("\\", "/")


def first_action(trace: RunTrace, action: Action) -> int | None:
    rx = re.compile(action.pattern)
    for c in trace.tools:
        if c.name in action.tools and rx.search(_target(c.input)):
            return c.index
    return None


def fired_before_action(trace: RunTrace, scenario: Scenario) -> bool | None:
    if not scenario.expect_skill or not scenario.action:
        return None
    at = first_action(trace, scenario.action)
    if at is None:
        return False
    return any(scenario.expect_skill in suggested_skills(h) and h.after_tool <= at for h in trace.hooks)


def noise(trace: RunTrace, scenario: Scenario) -> float:
    other = sum(1 for h in trace.hooks for s in suggested_skills(h) if s != scenario.expect_skill)
    return other / max(1, len(trace.tools))


def metrics(trace: RunTrace, scenario: Scenario, scored: dict) -> dict:
    return {**scored,
            "fired_before_action": fired_before_action(trace, scenario),
            "noise": noise(trace, scenario),
            "cost_usd": trace.cost,
            "turns": trace.turns}
