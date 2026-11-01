"""Scenarios: a sealed starting repo, a prompt that never names the rule, and a scorer.

A scenario's scorer reads only what the run produced (files in the repo, the parsed stream). It never
reads what the model claims, and nothing here calls a model.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from bench.stream import RunTrace

FAMILIES = ("skills", "precedent", "guards", "interjection")
POLARITIES = ("positive", "negative-control")
ARMS = ("bare", "written", "gnomonkit", "custom")
DEFAULT_TOOLS = ("Bash(python:*)", "Bash(python3:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(mkdir:*)")
MIN_POSITIVES = 3


@dataclass(frozen=True)
class Action:
    """The first tool call the rule was meant to shape: a tool name in `tools`, and `pattern`
    searched in the call's file path or command (backslashes normalised to slashes)."""
    tools: tuple[str, ...]
    pattern: str


@dataclass(frozen=True)
class Scenario:
    name: str
    family: str
    polarity: str
    files: dict[str, str]
    prompt: str
    score: Callable[[Path, "RunTrace"], dict[str, bool | None]]
    claude_md: tuple[str, ...] = ()
    hint_terms: tuple[str, ...] = ()
    expect_skill: str | None = None
    action: Action | None = None
    allowed_tools: tuple[str, ...] = DEFAULT_TOOLS
    arms: tuple[str, ...] = ARMS
    dirty: dict[str, str] | None = None     # written AFTER the initial commit: uncommitted work in the repo
    # Past answers reachable only through a search command, outside the repo:
    # {topic: {"keys": [words that make a query match], "text": "what the user said"}}
    history: dict[str, dict] | None = None
    # False: the history is searchable but CLAUDE.md never says so; only a hook can bring it in.
    announce_history: bool = True


REGISTRY: list[Scenario] = []


def register(s: Scenario) -> Scenario:
    REGISTRY.append(s)
    return s


def load_all() -> list[Scenario]:
    """Import every module in bench.scenarios (each registers its scenarios) and return them."""
    import bench.scenarios as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{mod.name}")
    return list(REGISTRY)


def validate(scenarios: list[Scenario]) -> list[str]:
    """Problems that would make results meaningless. Empty list = valid."""
    problems = []
    counts = Counter(s.name for s in scenarios)
    for name, n in counts.items():
        if n > 1:
            problems.append(f"duplicate scenario name '{name}' ({n}x)")
    for s in scenarios:
        if s.family not in FAMILIES:
            problems.append(f"'{s.name}': unknown family '{s.family}' (one of {', '.join(FAMILIES)})")
        if s.polarity not in POLARITIES:
            problems.append(f"'{s.name}': unknown polarity '{s.polarity}' (one of {', '.join(POLARITIES)})")
        bad_arms = [a for a in s.arms if a not in ARMS]
        if bad_arms:
            problems.append(f"'{s.name}': unknown arms {bad_arms}")
        for term in s.hint_terms:
            if re.search(rf"\b{re.escape(term)}\b", s.prompt, re.I):
                problems.append(f"'{s.name}': prompt names the rule (contains '{term}')")
    for fam in sorted({s.family for s in scenarios if s.family in FAMILIES}):
        members = [s for s in scenarios if s.family == fam]
        pos = sum(1 for s in members if s.polarity == "positive")
        neg = sum(1 for s in members if s.polarity == "negative-control")
        if pos < MIN_POSITIVES:
            problems.append(f"family '{fam}': needs at least {MIN_POSITIVES} positive scenarios, has {pos}")
        if neg < 1:
            problems.append(f"family '{fam}': needs at least one negative-control scenario")
    return problems
